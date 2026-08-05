// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Appearance category — theme triad, density, reduced-motion override.
 * Entirely device-local (`writer: { kind: "local" }`): no fetch, so no
 * loading/error states apply. The sidebar-footer theme button is a declared
 * mirror of the theme row (registry `mirrors: ["sidebar-footer"]`) — both
 * write the same store in `hooks/use-theme.ts`.
 */

import { Card, CardContent } from "@/components/ui/card"
import { SegmentedControl } from "@/components/ui/segmented-control"
import { useTheme, type Density, type MotionPreference, type ThemePreference } from "@/hooks/use-theme"
import { getDef } from "@/lib/settings-registry"
import { SettingRow } from "../settings-primitives"

function optionsOf<V extends string>(id: string): { value: V; label: string }[] {
  const def = getDef(id)
  return (def?.options ?? []).map((o) => ({ value: o.value as V, label: o.label }))
}

export default function AppearanceCategory() {
  const { preference, setPreference, density, setDensity, motion, setMotion } = useTheme()

  const themeDef = getDef("appearance.theme.mode")
  const densityDef = getDef("appearance.density.mode")
  const motionDef = getDef("appearance.motion.mode")
  if (!themeDef || !densityDef || !motionDef) return null

  return (
    <div className="density-stack">
      <Card>
        <CardContent className="pt-4">
          <h3 className="mb-1 text-label-xs font-medium tracking-wide text-muted-foreground uppercase">
            Theme
          </h3>
          <SettingRow def={themeDef}>
            <SegmentedControl<ThemePreference>
              value={preference}
              onChange={setPreference}
              options={optionsOf<ThemePreference>("appearance.theme.mode")}
              size="sm"
              ariaLabel={themeDef.label}
            />
          </SettingRow>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-4">
          <h3 className="mb-1 text-label-xs font-medium tracking-wide text-muted-foreground uppercase">
            Density
          </h3>
          <SettingRow def={densityDef}>
            <SegmentedControl<Density>
              value={density}
              onChange={setDensity}
              options={optionsOf<Density>("appearance.density.mode")}
              size="sm"
              ariaLabel={densityDef.label}
            />
          </SettingRow>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-4">
          <h3 className="mb-1 text-label-xs font-medium tracking-wide text-muted-foreground uppercase">
            Motion
          </h3>
          <SettingRow def={motionDef}>
            <SegmentedControl<MotionPreference>
              value={motion}
              onChange={setMotion}
              options={optionsOf<MotionPreference>("appearance.motion.mode")}
              size="sm"
              ariaLabel={motionDef.label}
            />
          </SettingRow>
        </CardContent>
      </Card>
    </div>
  )
}
