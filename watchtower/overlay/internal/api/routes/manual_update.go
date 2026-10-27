package routes

import (
	"context"
	"strings"

	"github.com/gofiber/fiber/v3"
	"github.com/gofiber/fiber/v3/middleware/timeout"

	"github.com/nicholas-fedor/watchtower/internal/api/config"
	"github.com/nicholas-fedor/watchtower/internal/api/handlers/update"
	mt "github.com/nicholas-fedor/watchtower/internal/metrics"
	"github.com/nicholas-fedor/watchtower/pkg/types"
)

// Dashboard-only manual updates use the same pipeline and lock as scheduled
// updates, with a request-local policy. They never change labels or global opts.
func registerManualUpdateRoute(ctx context.Context, app *fiber.App, auth fiber.Handler, opts config.Options) {
	updateTimeout := opts.UpdateTimeout
	if updateTimeout <= 0 {
		updateTimeout = config.DefaultUpdateTimeout
	}
	handler := update.New(opts.Logger, func(updateCtx context.Context, _ []string, names []string) *mt.Metric {
		if len(names) == 0 {
			return &mt.Metric{ScanFailed: true}
		}
		match := update.ContainerFilter(names)
		filter := func(c types.FilterableContainer) bool { return match(c.Name(), true) }
		params := config.BuildUpdateParams(opts)
		params.Filter = filter
		params.ManualUpdate = true
		params.MonitorOnly = false
		params.NoRestart = false
		params.LabelEnable = false
		params.ReviveStopped = false
		params.RollingRestart = false
		params.SkipSelfUpdate = false
		params.CooldownDelay = 0
		metric := opts.RunUpdatesWithNotifications(types.WithManualUpdate(updateCtx), filter, params)
		opts.DefaultMetrics().RegisterScan(metric)
		return metric
	}, opts.UpdateLock, updateTimeout, ctx)
	handler.Path = "/v1/manual-update"
	app.Post(handler.Path, auth, func(c fiber.Ctx) error {
		if strings.TrimSpace(c.Query("container")) == "" || c.Query("image") != "" || c.Query("async") != "" {
			return fiber.ErrBadRequest
		}
		return c.Next()
	}, timeout.New(handler.Handle, timeout.Config{Timeout: updateTimeout}))
}
