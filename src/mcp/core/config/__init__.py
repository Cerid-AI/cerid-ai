# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""core.config — pure declarations consumed by app-layer recommenders.

This sub-package contains decision-table registries that don't depend
on FastAPI, Redis, Neo4j, or any other runtime store. The recommender
job (:mod:`app.processor.jobs.config_recommender`) evaluates these
declarations against live corpus stats and writes the result to Redis
for the ``/health`` endpoint to surface.

Keeping the declarations in ``core/`` enforces the import-linter rule
(core never imports app) while still letting the app layer compose
them with infrastructure side-effects.
"""
