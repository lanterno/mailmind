// Step 0's one list: the inbox of the fictional mailbox (mm_fixtures::ACCOUNTS and FOLDER). It goes once the core
// can list a profile's accounts itself.
import type { ListQuery } from "./generated/ListQuery";

export const DEMO_INBOX: ListQuery = {
  accounts: ["work@example.test", "home@example.test", "club@example.test"],
  folder: "INBOX",
};

// The size of the mailbox the budgets are measured on; `?messages=N` asks for another on first load.
export const DEMO_MESSAGES = 100_000;
