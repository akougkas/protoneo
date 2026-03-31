/**
 * Lightweight markdown-to-HTML renderer for review content.
 * Handles headers, bold, italic, code, lists, and simple tables.
 * HTML-escapes input to prevent injection.
 */
export function renderMarkdown(text) {
  if (!text) return ''
  let html = text
    // Escape HTML
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    // Headers
    .replace(/^### (.+)$/gm, '<h4>$1</h4>')
    .replace(/^## (.+)$/gm, '<h3>$1</h3>')
    .replace(/^# (.+)$/gm, '<h2>$1</h2>')
    // Bold
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    // Italic
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    // Code
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    // Lists
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    .replace(/^(\d+)\. (.+)$/gm, '<li>$2</li>')
    // Simple table detection
    .replace(/^\|(.+)\|$/gm, (match, content) => {
      const cells = content.split('|').map(c => c.trim())
      if (cells.every(c => /^[-:]+$/.test(c))) return ''
      return '<tr>' + cells.map(c => `<td>${c}</td>`).join('') + '</tr>'
    })
    // Wrap consecutive <tr> in <table>
    .replace(/((?:<tr>.*<\/tr>\n?)+)/g, '<table class="md-table">$1</table>')
    // Paragraphs (double newlines)
    .replace(/\n\n/g, '</p><p>')
    // Single newlines to <br>
    .replace(/\n/g, '<br>')
  return '<p>' + html + '</p>'
}
