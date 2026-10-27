# AI assistant

> This page covers Kubernetes. HAProxy has its own assistant: see [HAProxy → AI assistant](./haproxy#ai-assistant).

The assistant investigates a whole cluster, one namespace, or one object. It finds what's wrong, explains the root cause with evidence, and proposes fixes. **It never changes anything by itself:** every proposed fix goes through the normal action flow, with review, dry run and audit.

## Set up a provider

You need the `settings.manage` permission. Open **Administration → System settings → AI providers**.

| Provider | What to enter |
|---|---|
| **Google Gemini** | An API key from Google AI Studio. The default model is `gemini-3.8-flash`. |
| **OpenRouter** | An API key from [openrouter.ai/keys](https://openrouter.ai/keys) and a model such as `vendor/model`. One key reaches models from many vendors; **Load models** lists only the ones that support tool calling. |
| **OpenAI-compatible** | An API key, a model that supports tool calling, and the base URL. CodeCraft API (`https://codecraftapi.com/v1`) is the default; any OpenAI-compatible API works. |

Click **Load models** to pick from the provider's list, then **Save & use**. You can save keys for every provider and switch with one click, for example when one provider is down. Keys are stored encrypted in the hub and never sent back to browsers.

## Investigate

You need the `ai.use` permission. Open **AI Assistant** and choose:
- **Cluster:** find every current problem;
- **Namespace:** one namespace;
- **Object:** a specific pod, deployment, etc. You can also start this from a row menu (**Diagnose with AI**) or **Ask AI** on the Overview.

Optionally add your own question, for example *"why is checkout slow since this morning?"*.

You get a **summary**, each **issue** with its root cause and evidence, and **proposed actions**: scale, restart, set image, roll back, delete a pod, or apply YAML. **Review & apply** opens the normal action dialog, with dry run, permissions, protected namespaces and audit. If the agent is read-only, you get the equivalent `kubectl` command instead.

## What the AI can see

The assistant uses read-only tools on the agent: the cluster overview and problems, pod lists, workloads, events, object specs, pod logs (including crashed containers) and deployment history. **It can't read Secret values**, and it can't change anything.

::: warning Data leaves your network
When you use the assistant, the data it reads (events, specs, log excerpts) is sent to the AI provider you chose. Check that this fits your company's policy before enabling it.
:::

Every investigation is recorded in the audit log as `ai-diagnose`, with the model used and how many issues were found.
