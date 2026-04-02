# Voice Agents — Project Instructions

## What this repo is

A multi-pattern voice agents showcase. Each agent pattern lives in its own folder. A root Streamlit app lets you select and demo any agent.

## Core rules

- **Always consult the LangChain MCP docs tool** (`mcp__docs-langchain__search_docs_by_lang_chain`) before implementing any LangChain/LangGraph component. Reference the canonical Python voice-agent page: https://docs.langchain.com/oss/python/langchain/voice-agent
- **Build component by component.** Do not proceed to the next component until the user has reviewed and approved the current one.
- **Everything runs in Docker.** Each agent folder has its own `Dockerfile` + `docker-compose.yml`. No local `pip install` workflows.
- **Python 3.11** across all agents.
- **Package manager:** `uv` (mirrors the upstream reference repo).
- **Do not add features beyond what is asked.** No speculative abstractions.

## Git workflow — mandatory for every new implementation

1. **Create a new branch** before starting any new agent pattern: `git checkout -b <agent-folder-name>`
2. Build and validate the implementation on that branch.
3. Once working, **commit** all changes with a descriptive message.
4. **Update `Architecture.md`** (at the repo root) with a section documenting the new pattern: architecture diagram, components, env vars, and how to run it.
5. Only then consider merging to `main`.

## Repo structure

```
voice-agents/
├── CLAUDE.md
├── Architecture.md               # Cumulative architecture docs (updated per implementation)
├── app.py                        # Root Streamlit selector app
├── docker-compose.yml            # Root compose (orchestrates all agents)
├── langchain-assembly-11labs-pipeline/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── pyproject.toml
│   ├── .env.example
│   ├── docs/
│   │   └── austin_trip.md        # Synthetic Austin trip schedule
│   └── src/
│       ├── events.py
│       ├── utils.py
│       ├── assemblyai_stt.py
│       ├── elevenlabs_tts.py
│       ├── agent.py
│       ├── pipeline.py
│       └── app.py                # Streamlit UI for this agent
└── <future-agent-pattern>/
```

## Agent 1 — `langchain-assembly-11labs-pipeline`

**Pattern:** STT > Agent > TTS (The "Sandwich")  
**Reference:** https://github.com/langchain-ai/voice-sandwich-demo  
**STT:** AssemblyAI (v3 WebSocket, 16kHz PCM)  
**Agent:** LangChain `create_agent` with `anthropic:claude-haiku-4-5`  
**TTS:** ElevenLabs (WebSocket streaming, PCM 24kHz)  
**Tracing:** LangSmith  
**UI:** Streamlit + `streamlit-webrtc` (Option A — real-time mic capture)  
**Demo domain:** Answers questions about a synthetic Austin trip schedule (`docs/austin_trip.md`)

## Environment variables (per agent)

```
ASSEMBLYAI_API_KEY=
ANTHROPIC_API_KEY=
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=          # default: 21m00Tcm4TlvDq8ikWAM (Rachel)
LANGCHAIN_API_KEY=            # LangSmith
LANGCHAIN_TRACING_V2=true
LANGCHAIN_PROJECT=voice-agents
```

## Event flow

```
UserInputEvent (PCM bytes)
  → STTChunkEvent (partial transcript)
  → STTOutputEvent (final transcript)
  → AgentChunkEvent (streamed token)
  → AgentEndEvent
  → TTSChunkEvent (PCM audio bytes)
```

## Key implementation notes

- Each pipeline stage is an `async def` generator wrapped in `RunnableGenerator`
- Stages run **concurrently** via `merge_async_iters` — TTS starts before agent finishes
- Agent uses `InMemorySaver` checkpointer for multi-turn conversation memory
- LangSmith tracing is enabled via env vars (no code changes needed)
- `streamlit-webrtc` feeds PCM frames into the pipeline; audio output is queued back to the browser
