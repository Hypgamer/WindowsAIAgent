from .chatgpt_auth import sign_in


def main():
    print("Windows AI Support Agent — Continue with ChatGPT")
    try:
        profile = sign_in()
    except Exception as exc:
        print(f"Sign-in failed: {exc}")
        raise SystemExit(1)
    email = profile.get("email") or "your ChatGPT account"
    print(f"Connected successfully as {email}.")
    print("ChatGPT plan usage is enabled for this local app.")


if __name__ == "__main__":
    main()
