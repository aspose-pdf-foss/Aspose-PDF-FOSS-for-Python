"""
Utility for safely renaming PDF resource names in content streams.
"""

from typing import Dict

def safe_rename_names(content: bytes, name_map: Dict[str, str]) -> bytes:
    """
    Safely rename PDF name objects in a content stream while skipping
    comments, literal strings, and hex strings.
    
    Args:
        content: The raw bytes of the PDF content stream.
        name_map: A dictionary mapping old names (without /) to new names (without /).
        
    Returns:
        The modified content stream bytes.
    """
    if not name_map:
        return content

    # Use a scanner approach to handle PDF syntax context
    res = bytearray()
    i = 0
    n = len(content)
    
    # PDF whitespace and delimiters for name termination
    # Delimiters: ( ) < > [ ] { } / %
    delimiters = b"()<>[]{}/%"
    whitespace = b" \t\n\r\f\0"
    terminators = delimiters + whitespace

    while i < n:
        b = content[i]
        
        if b == ord(b"%"):
            # Comment: skip until EOL
            while i < n and content[i] not in b"\r\n":
                res.append(content[i])
                i += 1
            continue
            
        if b == ord(b"("):
            # Literal string: handle nested parens and escapes
            res.append(b)
            i += 1
            depth = 1
            while i < n and depth > 0:
                char = content[i]
                res.append(char)
                if char == ord(b"\\"):
                    i += 1
                    if i < n:
                        res.append(content[i])
                elif char == ord(b"("):
                    depth += 1
                elif char == ord(b")"):
                    depth -= 1
                i += 1
            continue

        if b == ord(b"<"):
            # Hex string or dictionary start
            res.append(b)
            i += 1
            if i < n and content[i] == ord(b"<"):
                # Dictionary start << - just continue scanner
                res.append(content[i])
                i += 1
                continue
            
            # Hex string <...>
            while i < n and content[i] != ord(b">"):
                res.append(content[i])
                i += 1
            if i < n:
                res.append(content[i])
                i += 1
            continue

        if b == ord(b"/"):
            # Potential name object
            start_pos = i
            i += 1
            name_start = i
            while i < n and content[i] not in terminators:
                i += 1
            
            name_bytes = content[name_start:i]
            try:
                name_str = name_bytes.decode("latin1")
                if name_str in name_map:
                    new_name = name_map[name_str]
                    res.append(ord(b"/"))
                    res.extend(new_name.encode("latin1"))
                else:
                    res.extend(content[start_pos:i])
            except UnicodeDecodeError:
                res.extend(content[start_pos:i])
            continue

        # Regular byte
        res.append(b)
        i += 1

    return bytes(res)
