# Life-Boat production runtime

Life-Boat is served by the single multiplexed Hermes gateway. Its Telegram
credential must have exactly one polling owner; the `life-advisor` standalone
gateway service must remain stopped and disabled when the multiplexer serves
the profile.

## Release checks

- Confirm one `gateway run` process owns the Telegram credential.
- Confirm the Life-Boat profile is routed to topic `2` in chat
  `-1004230590253`.
- Confirm delayed follow-ups are disabled unless the user explicitly enabled
  them with `LIFEBOAT_PROACTIVE_FOLLOWUPS=1`; the bridge may run inertly.
- Confirm the only unsolicited schedule is the user-enabled morning flow.
- Run the Life-Boat verifier, strict focused tests, and a real cron delivery.
- Keep the raw response out of aggregate reports; inspect only bounded shape
  metrics and delivery status.

If a delivery reports `Telegram bot token already in use`, stop the standalone
`life-advisor` gateway before restarting the multiplexer. Never run both
gateways with the same Telegram token.

## Contact boundary

Life-Boat answers when the user writes and may run the separately enabled
morning check-in. Ordinary answers must not arm delayed follow-ups or
achievement nudges. The environment switch above is an explicit operational
opt-in for testing or a future user-facing preference; it is off by default.
