#!/usr/bin/env python3
"""Direct test of Edge TTS to see if the issue is with our code or Edge TTS API."""

import asyncio
import edge_tts

async def test_edge_tts():
    """Test Edge TTS directly with the same parameters."""
    print("Testing Edge TTS directly...")

    # Test 1: English voice (the one failing in tests)
    print("\n1. Testing en-US-AvaNeural with 'Hello':")
    try:
        text = "Hello"
        voice = "en-US-AvaNeural"
        rate = "+0%"

        print(f"   Text: '{text}'")
        print(f"   Voice: {voice}")
        print(f"   Rate: {rate}")

        communicator = edge_tts.Communicate(text=text, voice=voice, rate=rate)

        audio_chunks = []
        async for chunk in communicator.stream():
            if chunk["type"] == "audio":
                audio_chunks.append(chunk["data"])

        total_audio = sum(len(chunk) for chunk in audio_chunks)
        print(f"   Result: SUCCESS - Received {len(audio_chunks)} audio chunks, {total_audio} bytes total")

    except Exception as e:
        print(f"   Result: FAILED - {e}")
        import traceback
        traceback.print_exc()

    # Test 2: Italian voice (the one working in tests)
    print("\n2. Testing it-IT-GiuseppeMultilingualNeural with 'ciao':")
    try:
        text = "ciao"
        voice = "it-IT-GiuseppeMultilingualNeural"
        rate = "+0%"

        print(f"   Text: '{text}'")
        print(f"   Voice: {voice}")
        print(f"   Rate: {rate}")

        communicator = edge_tts.Communicate(text=text, voice=voice, rate=rate)

        audio_chunks = []
        async for chunk in communicator.stream():
            if chunk["type"] == "audio":
                audio_chunks.append(chunk["data"])

        total_audio = sum(len(chunk) for chunk in audio_chunks)
        print(f"   Result: SUCCESS - Received {len(audio_chunks)} audio chunks, {total_audio} bytes total")

    except Exception as e:
        print(f"   Result: FAILED - {e}")
        import traceback
        traceback.print_exc()

    # Test 3: Check if voice exists
    print("\n3. Checking available voices:")
    try:
        all_voices = await edge_tts.list_voices()
        ava_voices = [v for v in all_voices if 'AvaNeural' in v['ShortName'] and 'en-US' in v['ShortName']]
        giuseppe_voices = [v for v in all_voices if 'GiuseppeMultilingualNeural' in v['ShortName']]

        print(f"   en-US-AvaNeural voices found: {len(ava_voices)}")
        if ava_voices:
            print(f"   First match: {ava_voices[0]['ShortName']}")

        print(f"   it-IT-GiuseppeMultilingualNeural voices found: {len(giuseppe_voices)}")
        if giuseppe_voices:
            print(f"   First match: {giuseppe_voices[0]['ShortName']}")

    except Exception as e:
        print(f"   Result: FAILED - {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_edge_tts())

