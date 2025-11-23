#!/usr/bin/env python3
"""
Debug script to investigate the Italian voice alternating issue.
Run this against your production server with debug logs enabled.
"""

import requests
import hashlib
import sys
import time

BASE_URL = input("Enter server URL (default: http://localhost:5050): ").strip() or "http://localhost:5050"
API_KEY = input("Enter API key (default: your_api_key_here): ").strip() or "your_api_key_here"

def test_voice_with_logging(word="doppie", num_requests=10):
    """Test multiple consecutive requests and track what happens."""

    url = f"{BASE_URL}/v1/audio/speech"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}"
    }

    print(f"\n{'='*70}")
    print(f"Testing word: '{word}' with {num_requests} requests")
    print(f"Voice: it-IT-GiuseppeMultilingualNeural")
    print(f"{'='*70}\n")

    results = []

    for i in range(num_requests):
        payload = {
            "model": "tts-1",
            "voice": "it-IT-GiuseppeMultilingualNeural",
            "input": word,
            "response_format": "mp3",
            "speed": 1
        }

        print(f"Request {i+1}/{num_requests}...", end=" ", flush=True)

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)

            if response.status_code == 200:
                content = response.content
                content_hash = hashlib.md5(content).hexdigest()
                size = len(content)

                results.append({
                    "request_num": i + 1,
                    "hash": content_hash,
                    "size": size
                })

                print(f"✓ {size:5d} bytes | MD5: {content_hash}")
            else:
                print(f"✗ Error {response.status_code}: {response.text}")
                return
        except Exception as e:
            print(f"✗ Exception: {e}")
            return

        # Small delay between requests
        if i < num_requests - 1:
            time.sleep(0.3)

    # Analysis
    print(f"\n{'='*70}")
    print("ANALYSIS")
    print(f"{'='*70}\n")

    unique_hashes = {}
    for r in results:
        h = r["hash"]
        if h not in unique_hashes:
            unique_hashes[h] = []
        unique_hashes[h].append(r["request_num"])

    if len(unique_hashes) == 1:
        print("✓ SUCCESS: All requests produced IDENTICAL audio!")
        print(f"  Hash: {list(unique_hashes.keys())[0]}")
    else:
        print(f"✗ FAILURE: Found {len(unique_hashes)} DIFFERENT audio outputs:")
        for i, (h, req_nums) in enumerate(unique_hashes.items(), 1):
            count = len(req_nums)
            percentage = (count / num_requests) * 100
            request_nums_str = ", ".join(map(str, req_nums))
            print(f"\n  Variant {i}: {h}")
            print(f"    Frequency: {count}/{num_requests} ({percentage:.1f}%)")
            print(f"    Requests: {request_nums_str}")

        print(f"\n{'='*70}")
        print("NEXT STEPS:")
        print("{'='*70}")
        print("1. Check server logs for [TTS_DEBUG] messages")
        print("2. Verify edge-tts is receiving the correct voice parameter")
        print("3. Check if different variants have different file sizes")
        print("4. Try listening to the audio files to confirm language difference")

if __name__ == "__main__":
    test_voice_with_logging("doppie", 10)
    print("\n")

