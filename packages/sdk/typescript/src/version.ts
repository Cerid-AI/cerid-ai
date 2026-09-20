// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Wire-protocol version this client was built against — mirrors
 * `SDK_PROTOCOL_VERSION` in the Python SDK and `SDK_VERSION` on the server.
 *
 * Checked at run time against the version the server reports on its health and
 * settings responses (see `SystemResource`); a differing major version throws
 * `ProtocolVersionError` rather than letting payload skew pass silently.
 */
export const SDK_PROTOCOL_VERSION = "1.2.0";
