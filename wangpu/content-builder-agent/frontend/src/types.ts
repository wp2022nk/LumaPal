export interface ToolManifest {
  name: string;
  description: string;
  schema: Record<string, unknown>;
  source: string;
  configured?: boolean;
  mutates_workspace?: boolean;
  requires_approval?: boolean;
}

export interface SkillManifest {
  name: string;
  title: string;
  description: string;
  path: string;
  root: string;
}

export interface SubagentManifest {
  name: string;
  description: string;
  model?: string;
  tools: string[];
  skills: string[];
  prompt_preview: string;
}

export interface AgentManifest {
  agent: {
    name: string;
    assistant_id: string;
    model: string;
    thread_id: string;
    conversation: { max_turns?: number | null };
  };
  backend: {
    type: string;
    root_dir: string;
    virtual_mode: boolean;
    sandbox_python?: string | null;
    requires_execute_approval: boolean;
  };
  tools: ToolManifest[];
  subagents: SubagentManifest[];
  skills: SkillManifest[];
  artifacts: {
    roots: string[];
    output_root: string;
    preview_kinds: string[];
  };
}

export interface WorkspaceEntry {
  name: string;
  path: string;
  type: "directory" | "file";
  kind: string;
  mime: string;
  size: number;
  modified: string;
}

export interface ArtifactItem extends WorkspaceEntry {
  url: string;
}

export interface FilePayload {
  path: string;
  mime: string;
  encoding: "text" | "base64";
  content: string;
  size: number;
}

export interface PendingImage {
  id: string;
  name: string;
  size: number;
  dataUrl: string;
}

export interface SessionRecord {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
}

export interface TodoItem {
  content?: string;
  title?: string;
  status?: "pending" | "in_progress" | "completed" | string;
}

export interface AgentState {
  messages?: unknown[];
  todos?: TodoItem[];
  [key: string]: unknown;
}

export interface ToolCallView {
  id: string;
  name: string;
  args: Record<string, unknown>;
}

export interface ToolEventView {
  id: string;
  call: ToolCallView;
  result?: {
    content: string;
    status?: string;
  };
  tool?: ToolManifest;
  messageId?: string;
  running?: boolean;
}

export interface InterruptAction {
  action: string;
  args: Record<string, unknown>;
  description?: string;
}

export interface InterruptReview {
  allowedDecisions: string[];
}

export interface InterruptRequest {
  raw: unknown;
  action: InterruptAction;
  review: InterruptReview;
}

export interface SchemaField {
  name: string;
  type: string;
  required: boolean;
  description: string;
}

export type ActiveInspector = "plan" | "flow" | "artifacts" | "sandbox";
export type ApprovalMode = "review" | "edit" | "reject" | "respond";
export type ApprovalDecision = "approve" | "reject" | "edit" | "respond";
