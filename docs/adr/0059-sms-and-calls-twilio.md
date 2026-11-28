# ADR-059: SMS and calls through Twilio

## Context
The CRM (ADR-028) already prepared SMS and call campaigns with every guard in place:
- prior express consent recorded with evidence
- opt-outs, stored as hashes
- an 8am–9pm window in the recipient's time zone
- STOP wording in SMS, at most 3 segments
- a daily limit
- a person's approval
- a database trigger that refuses an unapproved, opted-out or unconsented send

Only the provider was missing, so every SMS/call was sent by the labeled MOCK.

## Decision
- **Connector.** `connectors/telephony.py` talks to Twilio's REST API. It authenticates with HTTP basic auth (account SID : auth token).
  - SMS: `POST /Accounts/{sid}/Messages.json` with `To`, `From`, `Body`.
  - Calls: `POST /Accounts/{sid}/Calls.json` with `To`, `From` and `Twiml=<Response><Say>script</Say></Response>`. The approved script is read out. It is XML-escaped, so a script can't inject TwiML. There is no recording.
- **Switching on.** `crm.sender_for` uses Twilio for `sms`/`call` when `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` and `TWILIO_FROM_NUMBER` are set. Otherwise it stays MOCK.
- **No double sends.** Twilio has no idempotency key. The existing claim step (`sending` before the provider call, never retried blindly) prevents a double send.
- **Launches never cover texts or calls.** The policy check now names the real channel. A Launch's standing approval only covers email lists, so every SMS/call campaign needs a person's approval.
- **Inbound STOP.** `/webhooks/twilio/{tenant}` handles STOP, UNSUBSCRIBE, CANCEL, END, QUIT, STOPALL, REVOKE and OPTOUT. The number is suppressed for SMS **and** calls. Twilio sends the carrier confirmation itself.
  - The request must carry a valid `X-Twilio-Signature`: HMAC-SHA1 with the auth token over the public URL plus the sorted parameters.
  - The request must also name the configured account.
- **Connections Test.** It reads the account and checks that `TWILIO_FROM_NUMBER` is on it. It sends nothing.

These are guard rails following the TCPA baseline, not legal advice. A2P 10DLC registration (US) is done in Twilio's console before texting at volume.

## What Steady does
1. Buy a number in Twilio.
2. Paste `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` (sealed) and `TWILIO_FROM_NUMBER` in Railway.
3. Set the number's messaging webhook to `https://<app>/webhooks/twilio/<tenant id>` (HTTP POST).
4. Press Test in Connections.
