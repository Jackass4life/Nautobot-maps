// Copy text for the operator (#530; first on the board): the clipboard API
// needs HTTPS (or localhost); plain HTTP falls back to the older copy
// command.  Whether the text was copied.
export async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Denied: try the fallback.
    }
  }
  const buffer = document.createElement("textarea");
  buffer.className = "clipboard-buffer";
  buffer.value = text;
  buffer.setAttribute("readonly", "");
  document.body.appendChild(buffer);
  buffer.select();
  let copied: boolean;
  try {
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  }
  buffer.remove();
  return copied;
}
