"""Sign in to the DGX Open WebUI and store a session token in the local .env file.

Use this when Open WebUI API keys are disabled for student accounts. Run it yourself in a
terminal; the password is read with getpass (not echoed) and is never written to disk.
Only the resulting session token is saved, to the git-ignored .env file.

  python -m src.login
"""
import getpass
import json
import urllib.error
import urllib.request

from src.config import DEFAULTS, REPO_ROOT, _read_dotenv

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def main():
    env_path = REPO_ROOT / ".env"
    current = _read_dotenv(env_path)
    base = current.get("OPENWEBUI_BASE_URL", DEFAULTS["OPENWEBUI_BASE_URL"]).rstrip("/")
    print(f"Signing in to {base}")
    email = input("Open WebUI email: ").strip()
    password = getpass.getpass("Open WebUI password (hidden): ")
    req = urllib.request.Request(
        base + "/api/v1/auths/signin",
        data=json.dumps({"email": email, "password": password}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with _OPENER.open(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Sign-in failed: HTTP {exc.code} {exc.read().decode('utf-8', 'replace')[:200]}")
    token = body.get("token")
    if not token:
        raise SystemExit("Sign-in succeeded but no token was returned.")

    lines = []
    if env_path.exists():
        lines = [l for l in env_path.read_text(encoding="utf-8").splitlines()
                 if not l.startswith("OPENWEBUI_TOKEN=")]
    else:
        lines = [l for l in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
                 if not l.startswith("OPENWEBUI_TOKEN=")]
    lines.append(f"OPENWEBUI_TOKEN={token}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Token saved to {env_path} (git-ignored). Role: {body.get('role')}. "
          f"Expires: {body.get('expires_at') or 'not set'}")


if __name__ == "__main__":
    main()
