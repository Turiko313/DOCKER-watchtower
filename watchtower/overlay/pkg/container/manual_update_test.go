package container

import (
	"testing"

	dockerContainer "github.com/moby/moby/api/types/container"
	"github.com/stretchr/testify/assert"

	"github.com/nicholas-fedor/watchtower/pkg/types"
)

func TestDashboardManualUpdateIgnoresMonitoringLabels(t *testing.T) {
	c := &Container{containerInfo: &dockerContainer.InspectResponse{
		Name: "/manual", Config: &dockerContainer.Config{Labels: map[string]string{
			monitorOnlyLabel: "true", noPullLabel: "true", cooldownDelayLabel: "24h",
		}},
	}}
	assert.True(t, c.IsMonitorOnly(types.UpdateParams{}))
	assert.True(t, c.IsNoPull(types.UpdateParams{}))
	params := types.UpdateParams{ManualUpdate: true}
	assert.False(t, c.IsMonitorOnly(params))
	assert.False(t, c.IsNoPull(params))
	assert.Zero(t, c.CooldownDelay(params))
	// An explicit process-level no-pull policy still supports local-only images.
	params.NoPull = true
	assert.True(t, c.IsNoPull(params))
}
