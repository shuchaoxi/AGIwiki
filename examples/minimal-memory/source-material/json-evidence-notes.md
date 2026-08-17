# AGIWiki JSON example evidence notes

This secondary note is maintained by the AGIWiki project and ships only to demonstrate Source,
Locator, and Entry binding. It is not a mirror of Python's official documentation and is not an
independent authority. Verify Python behavior against documentation for the matching version:
<https://docs.python.org/3.12/library/json.html>.

## Python json module facts

In Python 3.12, `json.dumps(..., ensure_ascii=False)` allows non-ASCII characters to appear
directly in the returned string; the default `True` escapes them.

`JSONDecodeError` exposes location information including `pos`, `lineno`, and `colno`. A location
shows where parsing stopped but does not establish the root cause by itself.

## Canonical JSON under the AGIWiki convention

AGIWiki's canonical JSON is a project-specific deterministic encoding convention: object keys use
a stable order, text uses UTF-8, separators are compact, and NaN, Infinity, and duplicate keys are
rejected. Equal JSON semantics should produce equal bytes under this convention, enabling stable
digests. Canonicalization does not replace JSON Schema and does not prove content is true.

## Safe JSON writing guidance

To produce readable UTF-8 JSON, first serialize with
`json.dumps(value, ensure_ascii=False, indent=2)`, then parse it again with `json.loads` and compare
the semantics. Write to a new UTF-8 file and verify the result before replacing an existing file.
Never write passwords, access tokens, or private keys, and do not blindly overwrite files or follow
an unverified symbolic link.

## JSONDecodeError troubleshooting guidance

Keep a read-only copy of the failing input and confirm that it should contain one JSON document,
not a log or JSON Lines. Inspect the exception's line, column, and character position. A failure at
the beginning can indicate an empty file, BOM, or encoding problem; a failure inside an object or
array often calls for checking quotes, commas, brackets, or a trailing comma. Correct only a copy,
parse it again, and verify the expected type and fields. Inputs can contain personal data or
credentials and must not be copied wholesale into public logs.
