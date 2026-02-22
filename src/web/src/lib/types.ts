// Chat types
export interface ChatMessage {
  id: string
  role: "user" | "assistant" | "system"
  content: string
  model?: string
  timestamp: number
}

export interface Conversation {
  id: string
  title: string
  messages: ChatMessage[]
  model: string
  createdAt: number
  updatedAt: number
}

// Model types
export interface ModelOption {
  id: string
  label: string
  provider: string
}

export const MODELS: ModelOption[] = [
  { id: "anthropic/claude-sonnet-4", label: "Claude Sonnet", provider: "Anthropic" },
  { id: "openai/gpt-4o", label: "GPT-4o", provider: "OpenAI" },
  { id: "openai/gpt-4o-mini", label: "GPT-4o Mini", provider: "OpenAI" },
  { id: "google/gemini-2.0-flash-exp:free", label: "Gemini Flash", provider: "Google" },
  { id: "x-ai/grok-4-fast", label: "Grok", provider: "xAI" },
  { id: "deepseek/deepseek-chat-v3-0324:free", label: "DeepSeek", provider: "DeepSeek" },
  { id: "meta-llama/llama-3.1-8b-instruct:free", label: "Llama 3.1", provider: "Meta" },
]

// Health types
export interface HealthResponse {
  status: "healthy" | "degraded"
  services: {
    chromadb: "connected" | "error"
    redis: "connected" | "error"
    neo4j: "connected" | "error"
  }
}

// Theme
export type Theme = "dark" | "light"
