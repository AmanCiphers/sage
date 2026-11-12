#!/usr/bin/env python3
"""Smoke-test the SAGE/Chatterbox TTS HTTP endpoint from the machine running this script."""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

TTS_URL = "http://192.168.1.54:8000/tts"
OUTPUT_FILE = Path("test_api.wav")
TEXT = "Hello Sir. This is SAGE. I can reach the text to speech server."


def main() -> int:
    payload = json.dumps({"text": TEXT}).encode("utf-8")
    request = urllib.request.Request(
        TTS_URL,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "audio/wav, audio/*, */*"},
        method="POST",
    )

    print(f"Calling TTS server: {TTS_URL}")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            audio = response.read()
            content_type = response.headers.get("Content-Type", "unknown")
            status = response.status
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP error: {exc.code} {exc.reason}")
        if details:
            print(f"Server response: {details[:1000]}")
        return 1
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"Could not reach TTS server: {exc}")
        print("Check that this machine can reach 192.168.1.54 and port 8000.")
        return 1

    if status != 200:
        print(f"Unexpected HTTP status: {status}")
        return 1
    if not audio:
        print("The server returned an empty response.")
        return 1

    OUTPUT_FILE.write_bytes(audio)
    print(f"Success: HTTP {status}; Content-Type: {content_type}")
    print(f"Received {len(audio):,} bytes; saved audio to: {OUTPUT_FILE.resolve()}")
    print("On macOS, play it with: afplay test_api.wav")
    return 0


if __name__ == "__main__":
    sys.exit(main())
