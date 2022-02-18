import sys
import json
import time
import threading

output_lines = open(sys.argv[-1]).read().split("\n")
input_lines = open(sys.argv[-2]).read().split("\n")
start_time = json.loads(output_lines[0])["params"]["timestamp"]


def playback():
    last_line = None
    last_time = None
    for line in output_lines:
        if line.strip():
            if not last_line:
                last_line = line
                last_time = json.loads(line).get("params", {}).get(
                    "timestamp"
                ) or json.loads(line).get("result", {}).get("timestamp")
            send_time = (
                json.loads(line).get("params") or json.loads(line).get("result")
            )["timestamp"]
            time.sleep((send_time - last_time) / 1000)
            print(line)


def check_response():
    for line in sys.stdin:
        line_contents = json.loads(line.strip() or "{}")
        line_contents.pop("id", None)
        input_line = json.loads(
            (input_lines.pop(0) if len(input_lines) else "{}") or "{}"
        )
        input_line.pop("id", None)
        print(input_line)
        print(line_contents)


if __name__ == "__main__":
    threading.Thread(target=check_response).start()
    playback()
