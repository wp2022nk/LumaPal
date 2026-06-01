import { useState } from "react";
import { Bot, ChevronDown, ChevronRight, Wrench } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { SubagentStreamInterface } from "@langchain/langgraph-sdk/ui";
import {
  isAssistant,
  messageImages,
  messageReasoning,
  messageRole,
  messageText,
  type AppMessage,
} from "../chat/messages";

export function MessageCard({
  message,
  subagents,
  onImageOpen,
}: {
  message: AppMessage;
  subagents: SubagentStreamInterface[];
  onImageOpen: (url: string) => void;
}) {
  const role = messageRole(message);
  const text = messageText(message);
  const reasoning = messageReasoning(message);
  const isUser = ["human", "user"].includes(role);

  return (
    <section className={`message-card ${isUser ? "user-message" : "agent-message"}`}>
      <div className="message-role">{isUser ? "你" : role === "tool" ? "工具结果" : "内容智能体"}</div>
      {reasoning && <Collapsible title="思考过程"><p className="reasoning">{reasoning}</p></Collapsible>}
      {text && role === "tool"
        ? (
            <Collapsible title={`工具结果：${message.name || "未命名工具"}`} icon={<Wrench size={15} />}>
              <article className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown></article>
            </Collapsible>
          )
        : text && <article className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown></article>}
      <div className="image-grid">
        {messageImages(message).map((url) => (
          <button className="image-tile" key={url} onClick={() => onImageOpen(url)}>
            <img src={url} alt="消息附件" />
          </button>
        ))}
      </div>
      {isAssistant(message) && message.tool_calls?.map((tool) => (
        <Collapsible key={tool.id || tool.name} title={`工具：${tool.name || "未命名"}`} icon={<Wrench size={15} />}>
          <pre>{JSON.stringify(tool.args, null, 2)}</pre>
        </Collapsible>
      ))}
      {subagents.map((subagent) => <SubagentCard key={subagent.id} subagent={subagent} />)}
    </section>
  );
}

export function SubagentCard({ subagent }: { subagent: SubagentStreamInterface }) {
  const label = String(subagent.toolCall?.args?.subagent_type || subagent.toolCall?.name || "子智能体");
  return (
    <Collapsible
      title={`${label} · ${subagent.status}`}
      icon={<Bot size={15} />}
      badge={subagent.status === "running" ? "执行中" : "已完成"}
    >
      <p className="muted">{String(subagent.toolCall?.args?.description || "")}</p>
      {subagent.messages.map((message, index) => (
        <div className="subagent-message" key={message.id || `${subagent.id}-${index}`}>
          <strong>{messageRole(message as AppMessage)}</strong>
          <span>{messageText(message as AppMessage)}</span>
        </div>
      ))}
      {subagent.result && <pre>{subagent.result}</pre>}
    </Collapsible>
  );
}

export function Collapsible({
  title,
  children,
  icon,
  badge,
  open = false,
}: {
  title: string;
  children: React.ReactNode;
  icon?: React.ReactNode;
  badge?: string;
  open?: boolean;
}) {
  const [expanded, setExpanded] = useState(open);
  return (
    <details className="collapsible" open={expanded} onToggle={(event) => setExpanded(event.currentTarget.open)}>
      <summary>
        {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        {icon}
        <span>{title}</span>
        {badge && <small>{badge}</small>}
      </summary>
      <div className="collapsible-body">{children}</div>
    </details>
  );
}
