import { Hellabit, type PayoutIntent } from "../sdk/hellabit.mjs";

const client = new Hellabit({ baseURL: "https://example.test", apiKey: "test-key" });
const intent: PayoutIntent = await client.payout("workspace", "intent");
const amount: number = intent.net_paid_units;
void amount;

// @ts-expect-error An intent ID is a string, never an amount.
await client.payout("workspace", 20);
