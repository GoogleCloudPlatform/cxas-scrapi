import sys

from cxas_scrapi import Sessions


def main():
    if len(sys.argv) < 2:
        print("Usage: python verify_personalization.py [APP_NAME]")
        sys.exit(1)

    app_name = sys.argv[1]
    session_client = Sessions(app_name)
    session_id = session_client.create_session_id()

    # Act: Start session with variable user_tier=GOLD
    print("Running session with user_tier=GOLD...")
    res = session_client.run(
        session_id=session_id,
        text="What offers do you have for me?",
        variables={"user_tier": "GOLD"},
    )

    # Assert: Check for VIP greeting and gold discounts
    structured = session_client.get_structured_response(res)
    agent_text = structured["agent_text"]
    print(f"Agent Response: {agent_text}")

    passed = True
    if "VIP customer" not in agent_text:
        print("FAIL: 'Welcome back VIP customer' not found in agent response.")
        passed = False
    if "gold discount" not in agent_text.lower():
        print("FAIL: 'gold discount' not found in agent response.")
        passed = False

    if passed:
        print("SUCCESS: Context personalization verified!")
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
