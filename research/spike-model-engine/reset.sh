#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
docker exec xt-spike-model-pg psql -q -U xt_owner -d postgres -c "drop database if exists xt with (force)" -c "drop role if exists xt_app" -c "drop role if exists xt_migrator" -c "drop role if exists xt_audit_owner" -c "create database xt"
docker exec -i xt-spike-model-pg psql -v ON_ERROR_STOP=1 -q -U xt_owner -d xt < /tmp/xt-spike-model/xtmodel/bootstrap.sql && echo BOOT_OK
