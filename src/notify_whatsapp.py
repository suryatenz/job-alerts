"""
WhatsApp ping via Twilio.
Requires: a Twilio account, a WhatsApp Sender enabled, and one APPROVED
content template (see README.md for the one-time setup steps). Until you've
done that, leave "twilio_enabled": false in config.json and this is skipped.
"""

from twilio.rest import Client


def send_whatsapp_alert(jobs, config):
    if not config.get("twilio_enabled", False):
        print("WhatsApp: twilio_enabled is false in config.json, skipping.")
        return

    account_sid = config.get("twilio_account_sid", "")
    auth_token = config.get("twilio_auth_token", "")
    from_number = config.get("twilio_whatsapp_from", "")
    to_number = config.get("twilio_whatsapp_to", "")
    content_sid = config.get("twilio_content_sid", "")

    if not all([account_sid, auth_token, from_number, to_number, content_sid]):
        print("WhatsApp: twilio_enabled is true but Twilio config fields are incomplete, skipping.")
        return

    client = Client(account_sid, auth_token)

    # Content template variables — must match the approved template's placeholders.
    # Example approved template body: "Your job digest is ready — {{1}} new matches today. Check your email for details."
    content_variables = {"1": str(len(jobs))}

    message = client.messages.create(
        from_=from_number,
        to=to_number,
        content_sid=content_sid,
        content_variables=str(content_variables).replace("'", '"'),
    )
    print(f"WhatsApp sent: {message.sid}")
