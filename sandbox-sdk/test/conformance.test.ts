import { runConformance } from "@opencoredev/sandbox-sdk/testing";
import { raft } from "../src/index.js";

// Live suite: creates and destroys real boxes. RAFT_LOCATION selects the host; RAFT_BIN can
// point at a development checkout of the CLI.
const location = process.env.RAFT_LOCATION;
if (!location) throw new Error("Set RAFT_LOCATION to the Raft location the live suite may use");

runConformance(
  () =>
    raft({
      location,
      bin: process.env.RAFT_BIN,
      ttlSeconds: 900,
      owner: "sandbox-sdk-raft conformance",
      next: "Destroyed by the conformance suite",
    }),
  { live: true },
);
