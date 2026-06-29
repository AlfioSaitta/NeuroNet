"""
Telegram text formatting utilities.
Estratto da tag_processor.py per modularizzazione.
"""

import re


def _escape_telegram_v2(text: str) -> str:
    """
    Escape caratteri speciali per Telegram MarkdownV2.
    Vanno escapati: _ * [ ] ( ) ~ ` > # + - = | { } . !

    MA non dentro costrutti markdown validi (già protetti).
    """
    special_chars = r'_*[]()~`>#+-=|{}.!'

    result = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in special_chars:
            if _is_inside_markdown_construct(text, i):
                result.append(ch)
            else:
                result.append('\\' + ch)
        else:
            result.append(ch)
        i += 1

    return ''.join(result)


def _escape_telegram_legacy(text: str) -> str:
    """
    Escape minimale per Telegram Markdown legacy.
    Previene entità non chiuse.
    """
    # Proteggi _ che non fanno parte di _italic_ valido
    text = re.sub(r'(?<!\w)_(?!\w)', r'\\_', text)
    text = re.sub(r'(?<=\s)_(?=\s)', r'\\_', text)
    text = re.sub(r'_(?=\d)', r'\\_', text)

    # Proteggi * non bilanciato
    star_indices = [i for i, c in enumerate(text) if c == '*']
    if len(star_indices) % 2 == 1:
        pos = star_indices[-1]
        text = text[:pos] + '\\*' + text[pos + 1:]

    # Proteggi ` non bilanciato
    bt_indices = [i for i, c in enumerate(text) if c == '`']
    if len(bt_indices) % 2 == 1:
        pos = bt_indices[-1]
        text = text[:pos] + '\\`' + text[pos + 1:]

    return text


def _is_inside_markdown_construct(text: str, pos: int) -> bool:
    """
    Determina se il carattere alla posizione `pos` è dentro un costrutto markdown valido.
    """
    if pos <= 0 or pos >= len(text) - 1:
        return False

    ch = text[pos]

    # Per * e _, controlla se sono parte di una coppia bilanciata
    if ch in ('*', '_'):
        before = text[:pos]
        after = text[pos + 1:]
        open_count = before.count(ch)
        close_count = after.count(ch)
        if open_count > 0 and close_count > 0:
            if open_count % 2 == 1 and close_count % 2 == 1:
                return True

    # Per `, controlla se fa parte di code inline
    if ch == '`':
        before = text[:pos]
        after = text[pos + 1:]
        if '`' in before and '`' in after:
            return True

    # Per ~, controlla se fa parte di ~~strikethrough~~
    if ch == '~':
        if pos + 1 < len(text) and text[pos + 1] == '~':
            return True
        if pos > 0 and text[pos - 1] == '~':
            return True

    # Per |, controlla se fa parte di ||spoiler||
    if ch == '|':
        if pos + 1 < len(text) and text[pos + 1] == '|':
            return True
        if pos > 0 and text[pos - 1] == '|':
            return True

    # Per [ ] ( ), controlla se fanno parte di [text](url)
    if ch in ('[', ']'):
        if ch == '[' and ']' in text[pos:]:
            return True
        if ch == ']' and '[' in text[:pos]:
            return True

    if ch == '(':
        if ')' in text[pos:]:
            return True

    if ch == ')':
        if '(' in text[:pos]:
            return True

    return False


def telegram_safe_format(text: str, use_markdown_v2: bool = False) -> str:
    """
    Converte testo preparato in formato compatibile con Telegram.
    DA USARE su ogni chunk PRIMA di inviare.

    use_markdown_v2=True  → escape per parse_mode='MarkdownV2'
    use_markdown_v2=False → escape minimale per parse_mode='Markdown'
    """
    if not text:
        return text

    # Proteggi blocchi di codice dall'escaping
    code_blocks: dict[str, str] = {}

    def _protect_code(m: re.Match) -> str:
        placeholder = f"__CB_{len(code_blocks)}__"
        code_blocks[placeholder] = m.group(0)
        return placeholder

    text = re.sub(r'```[\s\S]*?```', _protect_code, text)
    text = re.sub(r'(?<!`)`(?!`)([^`\n]+?)`(?!`)', _protect_code, text)

    if use_markdown_v2:
        text = _escape_telegram_v2(text)
    else:
        text = _escape_telegram_legacy(text)

    for placeholder, original in code_blocks.items():
        text = text.replace(placeholder, original)

    return text.strip()
