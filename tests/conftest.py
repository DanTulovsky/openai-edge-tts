import sys
import difflib
import _test_helpers as helpers

try:
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text
except Exception:
    Console = None
    Table = None
    Text = None


def pytest_runtest_makereport(item, call):
    # After the test call phase, if the test failed, print any stored side-by-side diff.
    if call.when == "call" and call.excinfo is not None:
        last = helpers.get_last_diff()
        if last:
            orig, trans = last
            console = Console(file=sys.__stdout__) if Console else None
            if console and Table and Text:
                # Build a human-friendly side-by-side table using word-level opcodes
                orig_words = orig.split()
                trans_words = trans.split()
                matcher = difflib.SequenceMatcher(None, orig_words, trans_words)
                table = Table(show_header=True, header_style="bold magenta")
                table.add_column("Original", overflow="fold")
                table.add_column("Transcribed", overflow="fold")

                for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                    left = " ".join(orig_words[i1:i2])
                    right = " ".join(trans_words[j1:j2])
                    if tag == 'equal':
                        table.add_row(left, right)
                    elif tag == 'replace':
                        left_text = Text(left, style="red")
                        right_text = Text(right, style="green")
                        table.add_row(left_text, right_text)
                    elif tag == 'delete':
                        left_text = Text(left, style="red")
                        table.add_row(left_text, "")
                    elif tag == 'insert':
                        right_text = Text(right, style="green")
                        table.add_row("", right_text)

                console.print(table)
            else:
                # Fallback: print unified diff to real terminal for visibility
                for line in difflib.unified_diff(orig.splitlines(), trans.splitlines(), lineterm=""):
                    try:
                        sys.__stdout__.write(line + "\n")
                    except Exception:
                        print(line)
            helpers.clear_last_diff()


