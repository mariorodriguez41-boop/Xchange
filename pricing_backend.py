import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


def load_local_env(env_path: str = ".env") -> None:
    """Load KEY=VALUE pairs from a local .env file."""
    if not os.path.exists(env_path):
        return

    try:
        with open(env_path, "r", encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    os.environ.setdefault(key, value)
    except OSError:
        return


load_local_env()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_LOOKUP_MODEL = os.getenv("OPENAI_SEARCH_MODEL", "gpt-4.1-mini")
BACKEND_HOST = os.getenv("PRICING_API_HOST", "127.0.0.1")
BACKEND_PORT = int(os.getenv("PRICING_API_PORT", "8765"))


def extract_search_sources(response) -> list[dict[str, str]]:
    """Pull source titles and URLs from an OpenAI web search response."""
    try:
        payload = response.model_dump()
    except Exception:
        return []

    sources: list[dict[str, str]] = []
    for item in payload.get("output", []):
        if item.get("type") == "web_search_call":
            action = item.get("action") or {}
            for source in action.get("sources", []) or []:
                title = source.get("title") or source.get("url") or "Source"
                url = source.get("url")
                if url and not any(existing["url"] == url for existing in sources):
                    sources.append({"title": title, "url": url})
    return sources[:3]


def lookup_live_price(search_term: str) -> dict:
    """Use OpenAI web search to estimate a live new retail price range."""
    if not OPENAI_API_KEY or OPENAI_API_KEY == "your_openai_api_key_here":
        raise RuntimeError("OPENAI_API_KEY is not configured on the pricing backend.")
    if OpenAI is None:
        raise RuntimeError("The OpenAI Python package is not installed on the pricing backend.")
    if not search_term.strip():
        raise ValueError("A search term is required.")

    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.responses.create(
        model=OPENAI_LOOKUP_MODEL,
        tools=[
            {
                "type": "web_search",
                "search_context_size": "medium",
                "user_location": {
                    "type": "approximate",
                    "country": "US",
                },
            }
        ],
        include=["web_search_call.action.sources"],
        instructions=(
            "Use live web search results to estimate the current United States new retail price range. "
            "Prefer current manufacturer pages and major retailers. Avoid resale, auction, and used-item pricing "
            "unless new retail pricing cannot be found."
        ),
        input=(
            f"Find the current typical new retail price in the United States for '{search_term}'. "
            "Use current web results in real time and synthesize a reasonable new-price range from the latest available listings. "
            "Reply in plain text with two lines only. "
            "Line 1 must be: PRICE_RANGE: $X - $Y "
            "Line 2 must be: SUMMARY: <short explanation mentioning the retailers or sources used>."
        ),
    )

    raw_text = getattr(response, "output_text", "").strip()
    if not raw_text:
        raise RuntimeError("The pricing model returned an empty response.")

    match = re.search(
        r"PRICE_RANGE:\s*\$?\s*([\d,]+(?:\.\d+)?)\s*-\s*\$?\s*([\d,]+(?:\.\d+)?)",
        raw_text,
        re.IGNORECASE,
    )
    summary_match = re.search(r"SUMMARY:\s*(.+)", raw_text, re.IGNORECASE | re.DOTALL)
    if not match:
        raise RuntimeError("The pricing model response could not be parsed.")

    low = float(match.group(1).replace(",", ""))
    high = float(match.group(2).replace(",", ""))
    summary = summary_match.group(1).strip() if summary_match else raw_text

    return {
        "price_range": f"${low:.0f} - ${high:.0f}",
        "summary": f"Live AI pricing: {summary}",
        "sources": extract_search_sources(response),
        "provider": "openai-web-search",
    }


class PricingRequestHandler(BaseHTTPRequestHandler):
    server_version = "CyberXchangePricing/1.0"

    def _send_json(self, status_code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(
                200,
                {
                    "status": "ok",
                    "backend": "pricing",
                    "openai_configured": bool(OPENAI_API_KEY and OPENAI_API_KEY != "your_openai_api_key_here"),
                },
            )
            return

        self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        if self.path != "/price-lookup":
            self._send_json(404, {"error": "Not found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0

        try:
            raw_body = self.rfile.read(content_length).decode("utf-8")
            payload = json.loads(raw_body or "{}")
            query = str(payload.get("query", "")).strip()
        except Exception:
            self._send_json(400, {"error": "Invalid JSON payload."})
            return

        if not query:
            self._send_json(400, {"error": "The 'query' field is required."})
            return

        try:
            result = lookup_live_price(query)
            self._send_json(200, result)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            self._send_json(503, {"error": str(exc)})

    def log_message(self, format_str: str, *args) -> None:
        """Keep the backend console output concise."""
        print(f"[pricing-backend] {self.address_string()} - {format_str % args}")


def run_server() -> None:
    server = ThreadingHTTPServer((BACKEND_HOST, BACKEND_PORT), PricingRequestHandler)
    print(f"Pricing backend listening on http://{BACKEND_HOST}:{BACKEND_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
