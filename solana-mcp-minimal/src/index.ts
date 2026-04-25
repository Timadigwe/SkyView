/**
 * Minimal Solana MCP: @modelcontextprotocol/sdk + @solana/kit.
 * Read-only chain tools.
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import {
  createSolanaRpc,
  address,
  isSolanaError,
  assertIsAddress,
} from "@solana/kit";
import { signature as toSignature } from "@solana/keys";

const CONFIG = {
  rpcUrl:
    process.env.SOLANA_RPC_URL ||
    process.env.SOLANA_RPC_ENDPOINT ||
    "https://api.devnet.solana.com",
};

const SPL = {
  TOKEN: address("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"),
  TOKEN_2022: address("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"),
};

const solanaRpc = createSolanaRpc(CONFIG.rpcUrl);

const JSON_MAX = 48_000;

function safeJson(obj: unknown): string {
  const s = JSON.stringify(
    obj,
    (_k, v) => (typeof v === "bigint" ? v.toString() : v),
    2
  );
  if (s.length <= JSON_MAX) return s;
  return s.slice(0, JSON_MAX) + "\n... [truncated]";
}

const server = new McpServer({
  name: "solana-mcp-minimal",
  version: "1.0.0",
});

server.tool(
  "getBalance",
  {
    walletAddress: z
      .string()
      .describe("Solana address to get native SOL balance for"),
  },
  async ({ walletAddress }) => {
    try {
      assertIsAddress(walletAddress);
      const addr = address(walletAddress);
      const { value: lamports } = await solanaRpc.getBalance(addr).send();
      const sol = Number(lamports) / 1_000_000_000;
      return {
        content: [
          {
            type: "text" as const,
            text: `Balance: ${sol} SOL (${lamports} lamports) for ${walletAddress}`,
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: `getBalance error: ${
              isSolanaError(error) ? error.message : String(error)
            }`,
          },
        ],
        isError: true,
      };
    }
  }
);

server.tool(
  "getTokenBalance",
  {
    ownerAddress: z
      .string()
      .describe("Wallet owner (same as your tracked wallet)"),
    mintAddress: z.string().describe("SPL token mint, e.g. USDC"),
  },
  async ({ ownerAddress, mintAddress }) => {
    try {
      assertIsAddress(ownerAddress);
      assertIsAddress(mintAddress);
    } catch {
      return {
        content: [{ type: "text" as const, text: "Invalid owner or mint address" }],
        isError: true,
      };
    }
    try {
      const accounts = await Promise.all([
        solanaRpc
          .getTokenAccountsByOwner(
            address(ownerAddress),
            { programId: SPL.TOKEN },
            { encoding: "jsonParsed" }
          )
          .send(),
        solanaRpc
          .getTokenAccountsByOwner(
            address(ownerAddress),
            { programId: SPL.TOKEN_2022 },
            { encoding: "jsonParsed" }
          )
          .send(),
      ]);
      const flat = [...accounts[0]!.value, ...accounts[1]!.value];
      const want = mintAddress;
      for (const acc of flat) {
        const data = (acc as { account: { data: { parsed?: { info?: { mint?: string; tokenAmount?: { uiAmount?: number | null } } } } } })
          .account.data;
        const mint = data.parsed?.info?.mint;
        if (mint === want) {
          const ui = data.parsed?.info?.tokenAmount?.uiAmount;
          if (ui != null) {
            return {
              content: [
                {
                  type: "text" as const,
                  text: `Token ${want} balance: ${ui} (ui units) for owner ${ownerAddress}`,
                },
              ],
            };
          }
        }
      }
      return {
        content: [
          {
            type: "text" as const,
            text: `No token account found for mint ${want} on ${ownerAddress} (0 balance or not created).`,
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: `getTokenBalance error: ${
              isSolanaError(error) ? error.message : String(error)
            }`,
          },
        ],
        isError: true,
      };
    }
  }
);

server.tool(
  "getAccountInfo",
  {
    address: z
      .string()
      .describe("Solana account address (base58)"),
  },
  async ({ address: addrStr }) => {
    try {
      assertIsAddress(addrStr);
    } catch {
      return {
        content: [{ type: "text" as const, text: "Invalid address" }],
        isError: true,
      };
    }
    try {
      const addr = address(addrStr);
      const res = await solanaRpc
        .getAccountInfo(addr, { encoding: "jsonParsed" })
        .send();
      const v = res.value;
      if (v == null) {
        return {
          content: [
            { type: "text" as const, text: `No account at ${addrStr} (or closed).` },
          ],
        };
      }
      const row = {
        owner: v.owner,
        lamports: v.lamports.toString(),
        executable: v.executable,
        rentEpoch: v.rentEpoch.toString(),
        data: v.data,
      };
      return {
        content: [{ type: "text" as const, text: safeJson(row) }],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: isSolanaError(error) ? error.message : String(error),
          },
        ],
        isError: true,
      };
    }
  }
);

server.tool(
  "getTransaction",
  {
    signature: z
      .string()
      .describe("Transaction signature (base58)"),
  },
  async ({ signature: sig }) => {
    try {
      const res = await solanaRpc
        .getTransaction(toSignature(sig), {
          commitment: "confirmed",
          encoding: "jsonParsed",
          maxSupportedTransactionVersion: 0,
        })
        .send();
      if (res == null) {
        return {
          content: [
            {
              type: "text" as const,
              text: `No transaction found for signature (wrong cluster, not yet confirmed, or invalid).`,
            },
          ],
        };
      }
      return {
        content: [{ type: "text" as const, text: safeJson(res) }],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: isSolanaError(error) ? error.message : String(error),
          },
        ],
        isError: true,
      };
    }
  }
);

server.tool(
  "getSignaturesForAddress",
  {
    address: z.string().describe("Account address to list recent signatures for"),
    limit: z
      .number()
      .min(1)
      .max(25)
      .optional()
      .describe("Max signatures (1–25, default 10)"),
  },
  async ({ address: addrStr, limit: lim }) => {
    try {
      assertIsAddress(addrStr);
    } catch {
      return {
        content: [{ type: "text" as const, text: "Invalid address" }],
        isError: true,
      };
    }
    const limit = lim ?? 10;
    try {
      const addr = address(addrStr);
      const rows = await solanaRpc
        .getSignaturesForAddress(addr, { limit, commitment: "confirmed" })
        .send();
      return {
        content: [{ type: "text" as const, text: safeJson(rows) }],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: isSolanaError(error) ? error.message : String(error),
          },
        ],
        isError: true,
      };
    }
  }
);

server.tool(
  "getSplTokenAccountCount",
  {
    ownerAddress: z
      .string()
      .describe("Wallet that holds SPL token accounts"),
  },
  async ({ ownerAddress }) => {
    try {
      assertIsAddress(ownerAddress);
    } catch {
      return {
        content: [{ type: "text" as const, text: "Invalid owner address" }],
        isError: true,
      };
    }
    try {
      const owner = address(ownerAddress);
      const accounts = await Promise.all([
        solanaRpc
          .getTokenAccountsByOwner(owner, { programId: SPL.TOKEN }, { encoding: "jsonParsed" })
          .send(),
        solanaRpc
          .getTokenAccountsByOwner(
            owner,
            { programId: SPL.TOKEN_2022 },
            { encoding: "jsonParsed" }
          )
          .send(),
      ]);
      const flat = [...accounts[0]!.value, ...accounts[1]!.value];
      return {
        content: [
          {
            type: "text" as const,
            text: `SPL token accounts (non-zero mints) for ${ownerAddress}: ${flat.length} accounts.`,
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: isSolanaError(error) ? error.message : String(error),
          },
        ],
        isError: true,
      };
    }
  }
);

server.tool("networkStatus", {}, async () => {
  try {
    await solanaRpc.getHealth().send();
  } catch {
    return { content: [{ type: "text" as const, text: "RPC health: fail" }], isError: true };
  }
  try {
    const { epoch, blockHeight, absoluteSlot } = await solanaRpc
      .getEpochInfo()
      .send();
    return {
      content: [
        {
          type: "text" as const,
          text: JSON.stringify(
            {
              health: "ok",
              epoch: epoch.toString(),
              blockHeight: blockHeight.toString(),
              slot: absoluteSlot.toString(),
            },
            null,
            2
          ),
        },
      ],
    };
  } catch (error) {
    return {
      content: [
        {
          type: "text" as const,
          text: isSolanaError(error) ? error.message : String(error),
        },
      ],
      isError: true,
    };
  }
});

async function runServer() {
  const t = new StdioServerTransport();
  await server.connect(t);
}

runServer().catch((e) => {
  console.error(e);
  process.exit(1);
});
