import { Hellabit, type PayoutIntent } from "../sdk/hellabit.mjs";

const client = new Hellabit({ baseURL: "https://example.test", apiKey: "test-key" });
const intent: PayoutIntent = await client.payout("workspace", "intent");
const amount: number = intent.net_paid_units;
void amount;
const queue = await client.payouts("workspace");
for (const connection of queue.connection_impact) {
  const total: bigint = BigInt(connection.held_units);
  const queued: bigint = BigInt(connection.holds.queued?.units ?? "0");
  void (total - queued);
}

// @ts-expect-error An intent ID is a string, never an amount.
await client.payout("workspace", 20);
