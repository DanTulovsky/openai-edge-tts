last_diff = None

def set_last_diff(original: str, transcribed: str):
    global last_diff
    last_diff = (original, transcribed)

def get_last_diff():
    return last_diff

def clear_last_diff():
    global last_diff
    last_diff = None


