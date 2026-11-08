import gemini from "../assets/logos/googlegemini.svg?url";
import cloudflare from "../assets/logos/cloudflare.svg?url";
import openai from "../assets/logos/openai.svg?url";
import bytedance from "../assets/logos/bytedance.svg?url";
import type { ModelInfo } from "../types/api";

/** Brand marks from Simple Icons (CC0, simple-icons@13.21.0), bundled locally. Drawn as a mask so they take the brand colour. */
export interface Logo { url: string; color: string; name: string }

const GEMINI: Logo = { url: gemini, color: "#8E75B2", name: "Google Gemini" };
const CLOUDFLARE: Logo = { url: cloudflare, color: "#F38020", name: "Cloudflare" };
const OPENAI: Logo = { url: openai, color: "#10A37F", name: "OpenAI" };
const BYTEDANCE: Logo = { url: bytedance, color: "#3C8CFF", name: "ByteDance" };

/** Logo of the company that serves the model; null for local / mock models (no logo, never a wrong one). */
export function modelLogo(m: Pick<ModelInfo, "provider_id" | "model_id">): Logo | null {
  if (/seedream/i.test(m.model_id)) return BYTEDANCE;
  if (m.provider_id === "gemini") return GEMINI;
  if (m.provider_id === "cloudflare") return CLOUDFLARE;
  if (m.provider_id === "openai") return OPENAI;
  return null;
}
