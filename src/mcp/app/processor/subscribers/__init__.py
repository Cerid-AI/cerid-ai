# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Event subscribers — handlers attached to processor event hooks.

Each module under this package registers one or more callbacks via
``app.processor.event_hooks.subscribe``. Importing this package on
app startup is sufficient to wire the full subscriber graph; the
event_hooks module itself imports each subscriber lazily.
"""
