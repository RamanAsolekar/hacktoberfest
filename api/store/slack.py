"""Slack approval message. STAND-IN by Person 1; Person 4 owns it.

Uses an incoming webhook with link buttons, so no Slack interactivity setup is needed:
Approve opens the web UI's incident page with ?action=approve.
"""

import logging

import httpx

from api.config import settings

log = logging.getLogger(__name__)


def notify_approval(incident: dict) -> None:
    if not settings.slack_webhook_url:
        return
    try:
        sym = incident.get("symptom") or {}
        ctx = incident.get("context") or {}
        match = incident.get("match") or {}
        fix = incident.get("fix") or {}
        if incident.get("source") == "memory":
            badge = f"From memory, {round(float(match.get('confidence') or 0) * 100)}% confident"
        else:
            badge = "Fresh diagnosis (no trusted memory match)"
        url = f"{settings.public_web_url}/incidents/{incident['id']}"
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": "Container failure needs approval"}},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{sym.get('error_message', 'unknown error')}*\n"
                    f"`{ctx.get('compose_project')}/{ctx.get('compose_service')}` exit {sym.get('exit_code')}",
                },
            },
            {"type": "context", "elements": [{"type": "mrkdwn", "text": badge}]},
            {"type": "section", "text": {"type": "mrkdwn", "text": match.get("explanation") or incident.get("root_cause") or ""}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Fix ({fix.get('type')})*\n```{fix.get('content')}```"}},
            {
                "type": "actions",
                "elements": [
                    {"type": "button", "style": "primary", "text": {"type": "plain_text", "text": "Approve"},
                     "url": f"{url}?action=approve&channel=slack"},
                    {"type": "button", "text": {"type": "plain_text", "text": "View"}, "url": url},
                ],
            },
        ]
        httpx.post(settings.slack_webhook_url, json={"text": "Container failure needs approval", "blocks": blocks}, timeout=10)
    except Exception as exc:  # Slack must never break the workflow
        log.warning("slack notify failed: %s", exc)
