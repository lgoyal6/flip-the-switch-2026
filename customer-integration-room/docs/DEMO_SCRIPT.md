# Customer Integration Room demo

## Story

Maya Chen buys one $129 backpack from Cedar Outfitters. Orbit Payments sends the
`order.paid` event. Cedar commits the fulfillment, but the response times out. Orbit
retries the event, and Cedar creates a second fulfillment.

## Recording sequence

1. Show Cedar with one payment and two fulfillment rows.
2. Show Orbit's delivery ledger with `evt_901` timing out, then retrying successfully.
3. Show Cedar's private worker finding the committed transaction and duplicate write.
4. Attempt to share a packet containing the customer email and secret canary. Show the export block.
5. Approve the redacted packets and show both findings in the Switch room.
6. Ask the human to choose the retry identity. Show `order_id` rejected because it would suppress address updates.
7. Approve `event_id` and the atomic idempotency patch.
8. Replay `evt_901` ten times concurrently. Show one fulfillment row.
9. Deliver `evt_902` and show the address update still succeeds.
10. Run `integration-v1` against the next customer. Show the vulnerable fixture failing and the repaired fixture passing.

## Spoken close

“Two companies had partial evidence. Switch gave them one shared case. The human chose
the business rule, the agents verified the repair, and the result became a test for the
next customer.”

## Commands

```text
@integration-coordinator start
@integration-coordinator approve evidence
@integration-coordinator decide order_id
@integration-coordinator decide event_id
@integration-coordinator approve patch
@integration-coordinator verify
```
