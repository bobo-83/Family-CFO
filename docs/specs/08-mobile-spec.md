# Mobile Spec

## Platform

iPhone app built with SwiftUI.

## Responsibilities

- Chat
- Camera capture
- Receipt capture
- Store item capture
- Face ID local unlock
- Notifications
- QR pairing with home server
- Secure authentication

## Non-Responsibilities

- Financial reasoning
- Long-term storage of household financial data
- Acting as the system of record

## Apple AI and Vision

Use Apple's long-term supported frameworks where available:

- Vision Framework
- Foundation Models when appropriate and available

The iPhone may summarize images into structured JSON before sending to the server.

Example:

```json
{
  "merchant": "Costco",
  "item": "MacBook Air",
  "price": {
    "amount_minor": 149900,
    "currency": "USD"
  },
  "confidence": 0.96
}
```

Photos should remain on device whenever structured extraction is sufficient.

## Pairing Flow

1. User opens dashboard onboarding.
2. Server displays QR code.
3. iPhone scans QR code.
4. App confirms server identity and household.
5. Server creates device credential.
6. App stores credential securely.

## Acceptance Criteria

- Mobile API client is generated from OpenAPI.
- Face ID protects local app access where available.
- Pairing credentials are revocable from the dashboard.
- Image capture sends structured JSON when possible.
