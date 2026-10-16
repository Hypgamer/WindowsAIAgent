from .ai_provider import get_provider


def main():
    print("Testing AI connection...")
    provider = get_provider()
    chunks = []
    completed = False
    with provider.client.responses.create(
        model=provider.model,
        input=[{"role": "user", "content": "Reply with exactly: AI connection OK"}],
        store=False,
        stream=True,
    ) as stream:
        for event in stream:
            if event.type == "response.output_text.delta":
                chunks.append(event.delta)
            elif event.type == "response.failed":
                error = event.response.error
                code = error.code if error else "unknown_error"
                raise RuntimeError(f"Connection test failed: {code}")
            elif event.type == "response.completed":
                completed = True
    if not completed:
        raise RuntimeError("Connection test ended without response.completed.")
    print("AI connection successful.")
    print(f"Provider: {provider.kind}")
    print(f"Model: {provider.model}")
    print(f"Response: {''.join(chunks).strip()}")


if __name__ == "__main__":
    main()
