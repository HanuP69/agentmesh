import http.server
import time


class HangHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        time.sleep(300)  # never actually respond within any client timeout

    def do_GET(self):
        time.sleep(300)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 8199), HangHandler)
    server.serve_forever()
