import { ImagePlus, PenLine, Globe, LucideIcon } from "lucide-react";

interface QuickAction {
  label: string;
  icon: LucideIcon;
}

const ACTIONS: QuickAction[] = [
  { label: "Create an image or sticker", icon: ImagePlus },
  { label: "Write or edit", icon: PenLine },
  { label: "Search the web", icon: Globe },
];

interface QuickActionsProps {
  onSelect: (label: string) => void;
}

export function QuickActions({ onSelect }: QuickActionsProps) {
  return (
    <div className="mt-6 flex flex-col items-start gap-1">
      {ACTIONS.map(({ label, icon: Icon }) => (
        <button
          key={label}
          onClick={() => onSelect(label)}
          className="flex items-center gap-3 rounded-xl px-3 py-2 text-left text-sm text-white/70 transition-colors hover:bg-white/5 hover:text-white"
        >
          <Icon size={18} className="text-white/50" />
          {label}
        </button>
      ))}
    </div>
  );
}
