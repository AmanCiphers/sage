#!/usr/bin/env python3
"""SQLite database backup utility for SAGE.

Creates a consistent backup of sage.db including WAL/SHM files.
Uses SQLite's built-in backup API for online backups.
"""

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def backup_database(src_db: Path, dst_dir: Path) -> Path:
    """Create a consistent backup of SQLite database using backup API.
    
    This uses SQLite's built-in backup API which handles WAL mode correctly
    and creates a consistent snapshot even if the database is in use.
    """
    dst_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst_db = dst_dir / f"sage_{timestamp}.db"
    
    # Use SQLite backup API for consistent backup
    src_conn = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    dst_conn = sqlite3.connect(dst_db)
    
    try:
        src_conn.backup(dst_conn)
    finally:
        src_conn.close()
        dst_conn.close()
    
    # Also copy WAL/SHM if they exist (for reference)
    for suffix in ["-wal", "-shm"]:
        src_file = src_db.with_suffix(src_db.suffix + suffix)
        if src_file.exists():
            dst_file = dst_dir / f"{dst_db.stem}{suffix}"
            shutil.copy2(src_file, dst_file)
    
    return dst_db


def restore_database(backup_db: Path, dst_db: Path) -> None:
    """Restore database from backup.
    
    This replaces the target database with the backup.
    Should only be run when SAGE is stopped.
    """
    if not backup_db.exists():
        raise FileNotFoundError(f"Backup not found: {backup_db}")
    
    # Remove existing WAL/SHM files
    for suffix in ["-wal", "-shm"]:
        wal_file = dst_db.with_suffix(dst_db.suffix + suffix)
        if wal_file.exists():
            wal_file.unlink()
    
    # Copy backup to destination
    shutil.copy2(backup_db, dst_db)
    
    print(f"Restored {backup_db} -> {dst_db}")


def list_backups(backup_dir: Path) -> list[Path]:
    """List available backups sorted by date (newest first)."""
    if not backup_dir.exists():
        return []
    backups = sorted(backup_dir.glob("sage_*.db"), reverse=True)
    return backups


def main():
    parser = argparse.ArgumentParser(description="SAGE Database Backup/Restore")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # Backup command
    backup_parser = subparsers.add_parser("backup", help="Create a database backup")
    backup_parser.add_argument("--src", type=Path, default=Path("data/sage.db"), help="Source database path")
    backup_parser.add_argument("--dst", type=Path, default=Path("backups"), help="Backup directory")
    
    # Restore command
    restore_parser = subparsers.add_parser("restore", help="Restore from backup")
    restore_parser.add_argument("backup", type=Path, help="Backup file to restore from")
    restore_parser.add_argument("--dst", type=Path, default=Path("data/sage.db"), help="Destination database path")
    
    # List command
    list_parser = subparsers.add_parser("list", help="List available backups")
    list_parser.add_argument("--dir", type=Path, default=Path("backups"), help="Backup directory")
    
    args = parser.parse_args()
    
    if args.command == "backup":
        dst = backup_database(args.src, args.dst)
        print(f"Backup created: {dst}")
    elif args.command == "restore":
        restore_database(args.backup, args.dst)
    elif args.command == "list":
        backups = list_backups(args.dir)
        if not backups:
            print("No backups found")
        else:
            for b in backups:
                size = b.stat().st_size
                mtime = datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                print(f"{b.name}  {size:,} bytes  {mtime}")


if __name__ == "__main__":
    main()