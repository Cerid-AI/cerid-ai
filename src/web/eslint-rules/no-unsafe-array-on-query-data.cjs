// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// ESLint rule: flag .map()/.filter()/.reduce() called directly on a useQuery
// data identifier without a ?? [] fallback or Array.isArray() guard.

"use strict"

/** @type {import('eslint').Rule.RuleModule} */
module.exports = {
  meta: {
    type: "problem",
    docs: { description: "Disallow .map/.filter/.reduce on useQuery data without null-fallback guard" },
    messages: {
      unsafeArrayOp:
        "{{method}}() called on '{{name}}' which may be undefined (useQuery data). " +
        "Add a fallback: ({{name}} ?? []).{{method}}() or safeArray({{name}}).{{method}}()",
    },
    schema: [],
  },
  create(context) {
    const safeIdentifiers = new Set()
    return {
      VariableDeclarator(node) {
        if (!node.init) return
        if (
          node.id.type === "ObjectPattern" &&
          node.init.type === "CallExpression" &&
          node.init.callee.name === "useQuery"
        ) {
          for (const prop of node.id.properties) {
            if (prop.type === "Property" && prop.value?.type === "AssignmentPattern") {
              safeIdentifiers.add(prop.value.left.name)
            }
          }
        }
        if (
          node.init.type === "LogicalExpression" &&
          node.init.operator === "??" &&
          node.init.right.type === "ArrayExpression" &&
          node.init.right.elements.length === 0 &&
          node.id.type === "Identifier"
        ) {
          safeIdentifiers.add(node.id.name)
        }
        if (
          node.init.type === "CallExpression" &&
          node.init.callee.name === "safeArray" &&
          node.id.type === "Identifier"
        ) {
          safeIdentifiers.add(node.id.name)
        }
      },
      CallExpression(node) {
        if (node.callee.type !== "MemberExpression") return
        const prop = node.callee.property
        if (!["map", "filter", "reduce"].includes(prop.name)) return
        const obj = node.callee.object
        let baseIdent = null
        if (obj.type === "Identifier") baseIdent = obj.name
        else if (obj.type === "ChainExpression" && obj.expression.type === "MemberExpression" && obj.expression.object.type === "Identifier") {
          baseIdent = obj.expression.object.name
        } else if (obj.type === "MemberExpression" && obj.object.type === "Identifier") {
          baseIdent = obj.object.name
        }
        if (!baseIdent) return
        if (safeIdentifiers.has(baseIdent)) return
        if (!/^data$|Data$|Resp$|Response$/.test(baseIdent)) return
        const parent = node.parent
        if (parent?.type === "LogicalExpression" && parent.operator === "??") return
        if (parent?.type === "ConditionalExpression") return
        context.report({ node, messageId: "unsafeArrayOp", data: { method: prop.name, name: baseIdent } })
      },
    }
  },
}
