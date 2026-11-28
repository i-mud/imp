const MAX_RAW_LENGTH = 512;
const CODE = /^imp1\.([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]{42}[AEIMQUYcgkosw048])$/;
const ENDPOINT = /^wss:\/\/([a-z0-9.-]+)(?::([1-9][0-9]{0,4}))?\/state$/;
const LABEL = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
const IPV4 = /^(?:0|[1-9][0-9]{0,2})(?:\.(?:0|[1-9][0-9]{0,2})){3}$/;

function validHost(host: string): boolean {
  if (/^[0-9.]+$/.test(host))
    return IPV4.test(host) && host.split('.').every((octet) => Number(octet) <= 255);
  const labels = host.split('.');
  return (
    host.length <= 253 &&
    labels.every((label) => LABEL.test(label) && !label.startsWith('xn--')) &&
    /^[a-z]/.test(labels.at(-1)!)
  );
}

function decodeEndpoint(encoded: string): string | null {
  if (encoded.length % 4 === 1) return null;
  let bytes: string;
  try {
    bytes = atob(encoded.replaceAll('-', '+').replaceAll('_', '/'));
  } catch {
    return null;
  }
  // Re-encoding rejects non-zero trailing bits, so each endpoint has one code form.
  const canonical = btoa(bytes).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '');
  return canonical === encoded && /^[\x20-\x7e]+$/.test(bytes) ? bytes : null;
}

/**
 * Parses an `imp1.<endpoint>.<token>` Direct connection code without repairing
 * or normalizing any part of it. Only the endpoint leaves this module so the
 * token cannot reach component state or rendering.
 */
export function parseConnectionCode(raw: string): string | null {
  if (raw.length > MAX_RAW_LENGTH) return null;
  const match = CODE.exec(raw.replace(/^[ \t\r\n]+|[ \t\r\n]+$/g, ''));
  const endpoint = match && decodeEndpoint(match[1]!);
  const parts = endpoint ? ENDPOINT.exec(endpoint) : null;
  if (!endpoint || !parts || !validHost(parts[1]!) || (parts[2] !== undefined && Number(parts[2]) > 65535)) {
    return null;
  }
  return endpoint;
}
