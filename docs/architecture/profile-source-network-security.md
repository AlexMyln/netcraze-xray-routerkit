# Profile source network security

Status: accepted for issue #23. This decision adds only the acquisition layer used by the offline profile parser delivered in #22. Automatic default setup source selection remains #24; parent issue #20 remains open.

## Decision and sources

The resolver is implementable with the Python 3.8+ standard library without a validation-to-connection DNS race. The design is based on the official documentation for [`socket`](https://docs.python.org/3/library/socket.html), [`ssl`](https://docs.python.org/3/library/ssl.html), [`http.client`](https://docs.python.org/3/library/http.client.html), [`ipaddress`](https://docs.python.org/3/library/ipaddress.html), [`multiprocessing`](https://docs.python.org/3/library/multiprocessing.html), and [`urllib.parse`](https://docs.python.org/3/library/urllib.parse.html), plus IETF [RFC 3986](https://www.rfc-editor.org/rfc/rfc3986.html), [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html), and [RFC 9112](https://www.rfc-editor.org/rfc/rfc9112.html).

`urllib.parse` explicitly does not validate URLs, so parsing is followed by an application-specific allowlist. `socket.getaddrinfo()` supplies IPv4/IPv6 TCP candidates but has no reliable per-call deadline. `multiprocessing` supplies `spawn`, IPC polling, termination, joining, and a stronger kill fallback. `ssl.create_default_context()` supplies required certificate validation and hostname checking; passing the original canonical hostname as `server_hostname` supplies SNI and the identity checked by TLS. `http.client` supplies bounded HTTP/1.1 request/response parsing while allowing a custom `connect()` implementation. `ipaddress` supplies address normalization and classification.

## Threat model

The source and every redirect response are untrusted. An attacker may provide confusing URL syntax, credentials in an authority, a token in a query, a hostname that resolves to local or special-use space, a mixed DNS answer, a rebinding answer, a redirect loop, an oversized or compressed body, slow DNS/TCP/TLS/body delivery, malformed HTTP framing, or secret-bearing error material. Ambient proxy variables, cookies, credentials, referrers, and debug logging are also treated as possible disclosure or routing channels.

The objective is a bounded GET of public HTTPS content without reaching loopback, private, link-local, multicast, unspecified, reserved, special-use, or otherwise non-global destinations. It is not a browser, an anonymity system, a content authenticity mechanism, or a defense against a legitimately public server returning malicious profile text. Payload validation remains the offline parser's responsibility.

## URL policy

Each initial and redirect URL is validated from scratch. The input is at most 8192 UTF-8 bytes and must contain no control characters, whitespace smuggling, backslashes, malformed percent escapes, or non-ASCII request-target characters. The only scheme is case-insensitive `https`, canonicalized to lowercase. An authority and hostname are required. Userinfo, fragments, empty ports, and ports other than 443 are rejected. Path and query are preserved because subscription tokens commonly use them; an empty path becomes `/` only for the HTTP request target.

Domain names are converted label-by-label with Python's built-in IDNA codec, lowercased, and checked against DNS length and label rules. A single terminal DNS dot is removed for canonical identity. IPv4 and bracketed IPv6 literals are accepted only after the destination policy passes and remain IP identities for normal TLS verification. Ambiguous numeric-looking names, IPv6 zone identifiers, malformed authorities, and host labels containing characters outside letters, digits, and interior hyphens are rejected. Literal and DNS host values are never included in public errors or object representations.

RFC 3986 defines authority, relative-reference resolution, and fragments; RFC 9110 defines `Location` as a URI-reference. `urllib.parse.urljoin()` is used only to combine a redirect reference with an already validated base. The combined absolute result is then revalidated rather than trusted.

## DNS deadline and destination policy

The main process never calls `getaddrinfo()` for a live source. It starts a short-lived top-level worker with the `spawn` multiprocessing context and passes the hostname and port through a one-way pipe, not custom process command-line arguments. The worker calls `getaddrinfo(AF_UNSPEC, SOCK_STREAM, IPPROTO_TCP)` and returns only normalized address-family/address pairs or a generic failure marker. The parent polls the pipe for at most 5 seconds per hop, bounded again by the 30-second overall deadline. On timeout or abnormal completion it terminates and joins the child, uses `kill()` plus another join if necessary, closes both pipe ends, and fails generically. Results are capped at 16 addresses.

`spawn` is available on supported POSIX Python versions and avoids running the resolver in the caller. The worker is a top-level importable function as required by `spawn`. A platform unable to start, terminate, or reap the worker fails closed. The timeout covers the blocking system resolver call; process startup itself remains an operating-system dependency.

Every returned address is normalized with `ipaddress`. The complete set is rejected if empty, oversized, malformed, or if any member is not `is_global`, or is private, loopback, link-local, multicast, unspecified, reserved, or IPv4-mapped IPv6. Rejecting the entire mixed set prevents an implementation from silently selecting only the convenient answers and makes DNS policy deterministic. Metadata and other special-use services fall under the same explicit non-global policy. Safe duplicates are removed and addresses are sorted deterministically by IP version and numeric value.

## Pinned TCP and verified TLS

After validation, no hostname-resolving HTTP connection is used. A small `http.client.HTTPConnection` subclass stores the canonical original hostname and one validated numeric address. Its `connect()` creates an `AF_INET` or `AF_INET6` TCP socket directly, applies the remaining bounded timeout, and connects to `(validated_ip, 443)`. It then wraps that socket with `ssl.create_default_context().wrap_socket(..., server_hostname=original_hostname)`. The code verifies that `check_hostname` is true and `verify_mode` is `CERT_REQUIRED`; it never creates or accepts an unverified context.

This split pins routing to the validated address while TLS SNI and certificate identity remain the original DNS name (or literal IP). After the TLS handshake, `getpeername()` is normalized and compared with the selected address. A mismatch closes the connection and fails. Candidate addresses from the one validated set may be tried in deterministic order, but no new name lookup occurs in the connection layer and all attempts share the overall deadline.

The HTTP request is GET in origin form with an explicit original-authority `Host`, `Accept-Encoding: identity`, a generic RouterKit user agent, and `Connection: close`. No proxy API or tunnel is used, environment proxy settings are never read, and no Cookie, Authorization, Proxy-Authorization, or Referer header is supplied.

## Redirect, response, and timeout policy

Only 301, 302, 303, 307, and 308 are followed, always as GET. At most 5 redirects are permitted. `Location` is required, limited to 8192 UTF-8 bytes, resolved relative to the current validated URL, and the result is validated and DNS-checked as a new hop. Canonical internal URL identities detect loops. Redirect response bodies are not processed and connections are closed. HTTPS cannot downgrade, port 443 cannot change, and headers are rebuilt for every hop.

Only a final 200 is accepted. `Content-Encoding` must be absent or `identity`; gzip, deflate, Brotli, and all other content codings are rejected and never decompressed. HTTP/1.1 chunked transfer framing is accepted through `http.client`, but other transfer codings, ambiguous `Transfer-Encoding` plus `Content-Length`, malformed lengths, and conflicting lengths are rejected. A declared length over 1 MiB is rejected before body processing; every body is still read with a 1 MiB + 1 sentinel and overflow is rejected. Text decoding is strict UTF-8; the standard UTF-8 BOM is accepted. Content-Type is not trusted for payload validity and only a sanitized broad media type may be retained internally.

One monotonic 30-second deadline covers all per-hop DNS waits, TCP connects, TLS handshakes, redirect processing, and body reads. DNS receives at most 5 seconds per hop and each address connection receives at most 10 seconds, always shortened to remaining overall time. Socket timeouts are reset to the remaining deadline before response/body operations. A short-lived daemon watchdog shuts down the active socket at the absolute overall deadline so a peer cannot evade it by trickling bytes before each inactivity timeout; bounded `HTTPResponse.read1()` calls also recheck the monotonic budget between body reads. The watchdog is cancelled and joined on every completed attempt. Responses and connections close on every path.

## Metadata and secret handling

Safe public output is limited to generic success or typed generic failure. URLs, hostnames, ports derived from redirects, paths, queries, `Location`, bodies, VLESS values, UUIDs, keys, short IDs, peer addresses, exception details, and response details are never interpolated into errors or logs. Secret-bearing dataclasses disable generated representations; the resolved payload representation excludes its text. Debug output is never enabled. Byte counts, redirect counts, and media type exist for internal testing/diagnostics but are not printed by the CLIs.

`profile-source --dry-run` is no-write, not no-network: an HTTPS input must be acquired and parsed before a selection can be validated. The later `routerkit setup --dry-run` behavior is unchanged and remains part of #24.

## Browser behavior intentionally excluded

Only standard HTTP redirects are followed. HTML meta refresh and JavaScript navigation require parsing or executing active browser content, introduce a much larger and less deterministic attack surface, and have no safe standard-library browser implementation. Cookies, cache state, referrers, authentication, content negotiation beyond identity encoding, and proxy inheritance are likewise excluded.

## Known limitations

- DNSSEC and certificate pinning are not added; the platform resolver and default CA trust store remain trusted inputs.
- A public destination can proxy or relay to other systems server-side; client-side destination validation cannot observe that.
- The overall deadline depends on Python socket timeouts and the operating system honoring process termination. Failure to create or reap the DNS worker fails closed.
- Python's built-in IDNA codec is used for compatibility with the supported standard-library baseline; this is not a browser-style WHATWG URL implementation.
- Only HTTP/1.1 over TLS on port 443, identity content encoding, and payloads up to 1 MiB are supported. HTTP/2, alternate ports, authentication, and browser shortlinks are outside this slice.
