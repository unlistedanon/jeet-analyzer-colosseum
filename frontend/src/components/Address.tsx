import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { shortAddress } from "../utils";

interface AddressProps {
  value?: string | null;
  full?: boolean;
}

export function Address({ value, full = false }: AddressProps) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      setCopied(false);
    }
  };
  return (
    <span className="address-value" title={value ?? "Unknown address"}>
      <code>{full ? value ?? "UNKNOWN" : shortAddress(value)}</code>
      {value && (
        <button className="icon-button" type="button" onClick={copy} aria-label={`Copy ${value}`}>
          {copied ? <Check size={14} /> : <Copy size={14} />}
        </button>
      )}
    </span>
  );
}
