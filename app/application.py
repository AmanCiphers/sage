from app.config import settings
from app.context.builder import ContextBuilder
from app.context.compressor import ContextCompressor
from app.conversation.loop import ConversationLoop
from app.conversation.models import AssistantResponse, UserMessage
from app.conversation.system_prompt import get_system_prompt
from app.embeddings import EmbeddingStore, create_primary_provider
from app.employees.budgets import Budget
from app.employees.supervisor import Supervisor
from app.events.bus import EventBus
from app.events.store import EventStore
from app.execution.engine import ExecutionEngine
from app.inference.manager import InferenceManager
from app.inference.models import ModelRequirements
from app.inference.providers.factory import create_provider
from app.inference.registry import ModelRegistry
from app.inference.router import ModelRouter
from app.logging import get_logger
from app.memory.store import MemoryStore
from app.notifications.completion import CompletionNotifier
from app.notifications.os import OsNotifier
from app.operations.manager import OperationManager
from app.permissions.approvals import ApprovalManager
from app.permissions.engine import PermissionEngine
from app.permissions.gate import ApprovalGate
from app.permissions.smart import get_smart_guardian
from app.persistence.database import Database
from app.persistence.schema import initialize_schema
from app.persistence.store import MessageStore, SessionStateStore, TaskStore
from app.scheduler.engine import SchedulerEngine
from app.scheduler.proactive import ProactiveEngine
from app.scheduler.store import SchedulerStore
from app.tools.builtin import register_builtin_tools
from app.tools.file_tools import register_file_tools
from app.tools.investigation import register_investigation_tools
from app.tools.memory_tools import register_memory_tools
from app.tools.registry import ToolRegistry
from app.tools.session_search import register_session_search
from app.tools.shell_tools import register_shell_tools
from config.model_catalog import get_model_catalog

# Import interfaces for DI
from app.interfaces import (
    IConversationEngine,
    IContextBuilder,
    IToolRegistry,
    IPermissionEngine,
    IWorkerPool,
    IAgentBackend,
    IMemoryStore,
    IScheduler,
    IProactiveEngine,
    IEventBus,
    IInferenceManager,
    IVerification,
)

# Global SAGE application instance (set during startup)
_sage_app: "SAGEApplication | None" = None


def get_sage_app() -> "SAGEApplication | None":
    """Get the global SAGE application instance."""
    return _sage_app


def set_sage_app(app: "SAGEApplication | None") -> None:
    """Set the global SAGE application instance."""
    global _sage_app
    _sage_app = app


class SAGEApplication:
    def __init__(
        self,
        # Interface implementations (for DI/testing)
        conversation_engine: IConversationEngine | None = None,
        context_builder: IContextBuilder | None = None,
        tool_registry: IToolRegistry | None = None,
        permission_engine: IPermissionEngine | None = None,
        worker_pool: IWorkerPool | None = None,
        agent_backends: dict[str, IAgentBackend] | None = None,
        memory_store: IMemoryStore | None = None,
        scheduler: IScheduler | None = None,
        proactive_engine: IProactiveEngine | None = None,
        event_bus: IEventBus | None = None,
        inference_manager: IInferenceManager | None = None,
        verification: IVerification | None = None,
    ) -> None:
        self.logger = get_logger("sage")

        self.database = Database(
            settings.database_path,
        )

        # Embedding provider & store for RAG (must be created before MessageStore)
        self.embedding_provider = create_primary_provider()
        self.embedding_store = None
        if self.embedding_provider:
            self.embedding_store = EmbeddingStore(self.database, self.embedding_provider)

        self.message_store = MessageStore(
            self.database,
            embedding_provider=self.embedding_provider,
            embedding_store=self.embedding_store,
        )
        self.session_state = SessionStateStore(self.database)
        self.task_store = TaskStore(self.database)

        # Use injected memory store or create default
        self.memory_store: IMemoryStore = memory_store or MemoryStore(
            settings.database_path.parent / "memory.md"
        )

        # Use injected tool registry or create default with toolset profile
        if tool_registry is None:
            from app.tools.toolsets import get_active_toolset
            active_toolset = get_active_toolset()
            self.tool_registry: IToolRegistry = ToolRegistry(toolset_profile=active_toolset)
        else:
            self.tool_registry: IToolRegistry = tool_registry

        self.model_registry = ModelRegistry()

        for metadata in get_model_catalog():
            try:
                provider = create_provider(metadata)
                self.model_registry.register(provider)
            except RuntimeError:
                # Provider is unavailable because its credentials are not configured.
                continue

        # Use injected inference manager or create default
        self.inference_manager: IInferenceManager = inference_manager or InferenceManager(
            self.model_registry
        )

        # Smart guardian (uses inference manager)
        self.smart_guardian = get_smart_guardian(self.inference_manager)

        # Approval gate (CLI callback)
        self.approval_gate = ApprovalGate(cli_callback=self._cli_approval_callback)

        # Use injected permission engine or create default
        self.permission_engine: IPermissionEngine = permission_engine or PermissionEngine(
            smart_guardian=self.smart_guardian,
            gateway_callback=self._gateway_approval_callback,
        )

        self.approval_manager = ApprovalManager(self.database)

        # Event system
        self.event_store = EventStore(self.database)
        self.event_bus: IEventBus = event_bus or EventBus(self.event_store)

        self.execution_engine = ExecutionEngine(
            self.tool_registry,
        )

        self.operations = OperationManager(
            database=self.database,
            execution_engine=self.execution_engine,
            registry=self.tool_registry,
        )

        # Supervisor: SAGE's interface to the employee roster (replaces the
        # old process-worker TaskDispatcher).
        # Use injected worker pool or create default Supervisor
        self.supervisor: IWorkerPool = worker_pool or Supervisor(
            task_store=self.task_store,
            permission_engine=self.permission_engine,
            registry=self.tool_registry,
            inference_manager=self.inference_manager,
            model_registry=self.model_registry,
            event_bus=self.event_bus,
            default_budget=Budget(
                max_iterations=settings.employee_max_iterations,
                max_tokens=settings.employee_max_tokens,
                max_wall_seconds=settings.employee_max_wall_seconds,
            ),
            max_result_chars=settings.max_tool_result_chars,
        )

        # Register builtin tools (includes assign_task, task_status, analyze, etc.)
        register_builtin_tools(
            self.tool_registry,
            supervisor=self.supervisor,
        )
        register_file_tools(self.tool_registry)
        register_shell_tools(self.tool_registry)
        register_memory_tools(self.tool_registry, self.memory_store)
        register_session_search(self.tool_registry, self.message_store)
        register_investigation_tools(self.tool_registry)

        # Scheduler & Proactive
        self.scheduler_store = SchedulerStore(self.database)
        self.scheduler: IScheduler = scheduler or SchedulerEngine(
            store=self.scheduler_store,
            event_bus=self.event_bus,
            check_interval_seconds=settings.scheduler_check_interval,
        )
        self.proactive: IProactiveEngine = proactive_engine or ProactiveEngine(
            store=self.scheduler_store,
            event_bus=self.event_bus,
            execution_engine=self.execution_engine,
            tool_registry=self.tool_registry,
        )

        # Collects employee-finished notices for delivery between turns.
        self.completion_notifier = CompletionNotifier()
        # Sends silent OS notifications for scheduled/proactive/reminder/background events.
        self.os_notifier = OsNotifier()

        # Wire up tool handlers that need scheduler/proactive
        self._wire_tool_handlers()
        # Determine the effective context window from the primary model
        # (highest-priority available model with tool_calling support).
        router = ModelRouter(self.model_registry)
        primary_model = router.route(ModelRequirements(tool_calling=True))
        effective_context_window = primary_model.metadata.context_window

        # Use injected compressor or create default
        self.compressor = ContextCompressor(
            inference_manager=self.inference_manager,
            session_state=self.session_state,
            context_window=effective_context_window,
            threshold_ratio=settings.compaction_threshold_ratio,
        )

        # Use injected context builder or create default
        self.context_builder: IContextBuilder = context_builder or ContextBuilder(
            message_store=self.message_store,
            session_state=self.session_state,
            compressor=self.compressor,
            system_prompt=get_system_prompt(self.memory_store),
            history_fetch_limit=settings.history_fetch_limit,
            memory_store=self.memory_store,
            embedding_store=self.embedding_store,
            embedding_provider=self.embedding_provider,
        )

        # Use injected conversation engine or create default
        self.conversation_engine: IConversationEngine = ConversationLoop(
            inference_manager=self.inference_manager,
            tool_registry=self.tool_registry,
            permission_engine=self.permission_engine,
            execution_engine=self.execution_engine,
            approval_manager=self.approval_manager,
            message_store=self.message_store,
            context_builder=self.context_builder,
            compressor=self.compressor,
            max_iterations=settings.max_iterations,
            max_tool_result_chars=settings.max_tool_result_chars,
        )

    def _wire_tool_handlers(self) -> None:
        """Wire up handlers for tools that need scheduler/proactive."""
        # assign_task / task_status handlers are registered by
        # register_builtin_tools(supervisor=...) and need no rewiring here.

        # Scheduler tools need scheduler
        scheduler_tools = {
            "schedule_job": lambda **kw: self.scheduler.add_job(
                timezone=kw.pop("timezone", None) or settings.timezone, **kw
            ),
            "list_jobs": lambda enabled_only=False: self._list_jobs_json(enabled_only),
            "enable_job": lambda job_id, enabled=True: self._enable_job_json(job_id, enabled),
            "remove_job": lambda job_id: self._remove_job_json(job_id),
        }
        for name, handler in scheduler_tools.items():
            try:
                tool = self.tool_registry.get(name)
                tool.handler = handler
            except KeyError:
                # Tool not in current toolset, skip
                pass

        # Proactive tools need proactive engine
        proactive_tools = {
            "add_proactive_rule": lambda **kw: self.proactive.add_rule(**kw),
            "list_rules": lambda enabled_only=True: self._list_rules_json(enabled_only),
            "enable_rule": lambda rule_id, enabled=True: self._enable_rule_json(rule_id, enabled),
            "remove_rule": lambda rule_id: self._remove_rule_json(rule_id),
        }
        for name, handler in proactive_tools.items():
            try:
                tool = self.tool_registry.get(name)
                tool.handler = handler
            except KeyError:
                # Tool not in current toolset, skip
                pass

    async def _list_jobs_json(self, enabled_only: bool = False) -> str:
        import json
        jobs = await self.scheduler.list_jobs()
        if enabled_only:
            jobs = [j for j in jobs if j.enabled]
        result = []
        for job in jobs:
            result.append({
                "job_id": job.job_id,
                "name": job.name,
                "cron": job.cron_expression,
                "event_type": job.event_type,
                "enabled": job.enabled,
                "next_run": job.next_run_at,
                "run_count": job.run_count,
            })
        return json.dumps({"jobs": result})

    async def _enable_job_json(self, job_id: str, enabled: bool = True) -> str:
        import json
        success = await self.scheduler.enable_job(job_id, enabled)
        return json.dumps({"success": success, "job_id": job_id})

    async def _remove_job_json(self, job_id: str) -> str:
        import json
        success = await self.scheduler.remove_job(job_id)
        return json.dumps({"success": success, "job_id": job_id})

    async def _list_rules_json(self, enabled_only: bool = True) -> str:
        import json
        rules = await self.proactive.list_rules()
        if enabled_only:
            rules = [r for r in rules if r.enabled]
        result = []
        for rule in rules:
            result.append({
                "rule_id": rule.rule_id,
                "name": rule.name,
                "description": rule.description,
                "trigger": rule.trigger_event_type,
                "action": rule.action_event_type,
                "enabled": rule.enabled,
                "cooldown": rule.cooldown_seconds,
                "fire_count": rule.fire_count,
            })
        return json.dumps({"rules": result})

    async def _enable_rule_json(self, rule_id: str, enabled: bool = True) -> str:
        import json
        success = await self.proactive.enable_rule(rule_id, enabled)
        return json.dumps({"success": success, "rule_id": rule_id})

    async def _remove_rule_json(self, rule_id: str) -> str:
        import json
        success = await self.proactive.remove_rule(rule_id)
        return json.dumps({"success": success, "rule_id": rule_id})

    async def _cli_approval_callback(self, prompt) -> "ApprovalResponse":  # noqa: F821
        """CLI approval callback - prompts user for approval."""
        from app.permissions.models import ApprovalResponse

        print(f"\n⚠️  {prompt.description}")
        print(f"Target: {prompt.display_target}")
        print()
        print("Allow this action?")
        print("  [y] Yes, once")
        if prompt.allow_session:
            print("  [s] Yes, for this session")
        if prompt.allow_permanent:
            print("  [a] Yes, permanently (add to allowlist)")
        print("  [n] No, deny")
        print("  [q] Quit")

        choice = input("Choice: ").strip().lower()

        if choice in ("y", "yes"):
            return ApprovalResponse(choice="allow")
        elif choice in ("s", "session") and prompt.allow_session:
            return ApprovalResponse(choice="session")
        elif choice in ("a", "always", "permanent") and prompt.allow_permanent:
            return ApprovalResponse(choice="permanent")
        elif choice in ("q", "quit"):
            return ApprovalResponse(choice="cancelled", reason="User quit")
        else:
            return ApprovalResponse(choice="deny", reason="User denied")

    async def _gateway_approval_callback(self, prompt) -> "ApprovalResponse":  # noqa: F821
        """Gateway approval callback - for future Web UI integration."""
        from app.permissions.models import ApprovalResponse
        # Placeholder for gateway (Web UI) approval
        # For now, fall back to pending approval
        return ApprovalResponse(choice="cancelled", reason="Gateway not implemented")

    async def approve_pending(
        self,
        approval_id: str,
        on_delta=None,
    ) -> "AssistantResponse":
        """Approve the bound action, execute it as an operation, then
        feed the result back into the conversation."""

        approval = await self.approval_manager.approve(approval_id)

        operation = await self.operations.execute(
            approval["tool_name"],
            approval["arguments"],
            approval_id=approval_id,
        )

        self.logger.info(
            "approval_executed approval_id=%s operation_id=%s status=%s",
            approval_id,
            operation["operation_id"],
            operation["status"],
        )

        summary = operation["result"] if operation["status"] == "SUCCEEDED" else (
            operation["error"] or operation["status"]
        )

        message = UserMessage(
            content=(
                f"The user APPROVED the pending action "
                f"({approval['tool_name']}). It was executed. "
                f"Operation status: {operation['status']}. "
                f"Result: {summary}. Confirm what was done."
            )
        )

        if on_delta is not None:
            return await self.conversation_engine.handle_stream(message, on_delta)

        return await self.conversation_engine.handle(message)

    async def deny_pending(
        self,
        approval_id: str,
        on_delta=None,
    ) -> "AssistantResponse":
        await self.approval_manager.deny(approval_id)

        self.logger.info("approval_denied approval_id=%s", approval_id)

        message = UserMessage(
            content=(
                "The user DENIED the pending action. It was not "
                "performed. Acknowledge the denial."
            )
        )

        if on_delta is not None:
            return await self.conversation_engine.handle_stream(message, on_delta)

        return await self.conversation_engine.handle(message)

    async def start(self) -> None:
        set_sage_app(self)

        self.logger.info(
            "starting_sage",
            environment=settings.environment,
        )

        await self.database.connect()
        await initialize_schema(self.database)

        # Initialize employee databases
        await self._initialize_employee_databases()

        # Initialize event system
        await self.event_store.initialize()

        # Initialize scheduler tables
        await self.scheduler_store.initialize()

        # Initialize proactive engine
        await self.proactive.initialize()

        # Wire proactive engine to event bus
        self.event_bus.subscribe_all(self.proactive.handle_event)

        # Wire completion notices to the event bus
        self.event_bus.subscribe_all(self.completion_notifier.handle_event)

        # Wire OS notifications (silent) to the event bus
        self.event_bus.subscribe_all(self.os_notifier.handle_event)

        settings.workspace_path.expanduser().mkdir(parents=True, exist_ok=True)

        # Auto-authorize the main workspace so SAGE can access it without explicit permission
        from app.tools.workspace import authorize_workspace
        authorize_workspace(settings.workspace_path.expanduser().resolve())

        recovered = await self.operations.recover()
        if recovered:
            self.logger.warning("stale_operations_recovered count=%s", recovered)

        stale_tasks = await self.supervisor.recover()
        if stale_tasks:
            self.logger.warning("stale_assignments_recovered count=%s", stale_tasks)

        # Start scheduler
        await self.scheduler.start()

        self.logger.info(
            "database_ready",
            database=str(settings.database_path),
        )

        self.logger.info(
            "sage_ready",
            tools=[
                tool.name
                for tool in self.tool_registry.list()
            ],
        )

    async def stop(self) -> None:
        self.logger.info("shutting_down_sage")

        # Stop scheduler
        await self.scheduler.stop()

        # Wait for outstanding employee assignments, then close their stores.
        await self.supervisor.shutdown()

        # Close embedding provider
        if self.embedding_provider:
            await self.embedding_provider.close()

        await self.database.close()

        set_sage_app(None)

        self.logger.info("sage_stopped")

    async def _initialize_employee_databases(self) -> None:
        """Initialize all employee databases at startup so tools can access them."""
        from app.config import settings
        from app.employees.roster import ROSTER
        from app.persistence.database import Database
        from app.persistence.schema import initialize_schema

        self.logger.info("initializing_employee_databases")

        for spec in ROSTER:
            try:
                root = settings.database_path.parent / "employees" / spec.key
                root.mkdir(parents=True, exist_ok=True)
                (root / "workspace").mkdir(parents=True, exist_ok=True)

                db_path = root / "sage.db"
                database = Database(db_path)
                await database.connect()
                await initialize_schema(database)
                await database.close()

                self.logger.info("employee_database_initialized", worker=spec.key)
            except Exception as e:
                self.logger.warning("employee_database_init_failed", worker=spec.key, error=str(e))

        self.logger.info("all_employee_databases_initialized")
