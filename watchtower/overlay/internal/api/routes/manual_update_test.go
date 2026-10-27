package routes

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/nicholas-fedor/watchtower/internal/api/config"
	"github.com/nicholas-fedor/watchtower/internal/metrics"
	"github.com/nicholas-fedor/watchtower/internal/scheduling"
	"github.com/nicholas-fedor/watchtower/pkg/container/mocks"
	"github.com/nicholas-fedor/watchtower/pkg/types"
)

func TestDashboardManualUpdatePolicy(t *testing.T) {
	app := testApp()
	rejected := func(types.FilterableContainer) bool { return false }
	opts := config.Options{
		Filter: rejected,
		BaseParams: types.UpdateParams{Filter: rejected, MonitorOnly: true, NoRestart: true, LabelEnable: true},
		DefaultMetrics: func() *metrics.Metrics { return testMetrics },
		UpdateLock: scheduling.NewUpdateLock(),
	}
	called := false
	opts.RunUpdatesWithNotifications = func(ctx context.Context, filter types.Filter, params types.UpdateParams) *metrics.Metric {
		called = true
		assert.True(t, types.IsManualUpdate(ctx))
		assert.True(t, params.ManualUpdate)
		assert.False(t, params.MonitorOnly)
		assert.False(t, params.NoRestart)
		assert.False(t, params.LabelEnable)
		for _, name := range []string{"/app.one", "/appXone", "/app.one-other"} {
			c := new(mocks.FilterableContainer)
			c.On("Name").Return(name)
			assert.Equal(t, name == "/app.one", filter(c))
			assert.Equal(t, name == "/app.one", params.Filter(c))
		}
		return &metrics.Metric{Scanned: 1}
	}
	registerManualUpdateRoute(t.Context(), app, testAuthMiddleware(), opts)
	req := httptest.NewRequest(http.MethodPost, "/v1/manual-update?container=%5Eapp%5C.one%24", nil)
	req.Header.Set("Authorization", "Bearer test")
	response, err := app.Test(req)
	require.NoError(t, err)
	defer response.Body.Close()
	assert.Equal(t, http.StatusOK, response.StatusCode)
	assert.True(t, called)
	assert.True(t, opts.BaseParams.MonitorOnly)
	assert.True(t, opts.BaseParams.LabelEnable)
	assert.False(t, opts.Filter(nil))
	assert.False(t, types.IsManualUpdate(context.Background()))
}

func TestDashboardManualUpdateRejectsUntargetedRequests(t *testing.T) {
	app := testApp()
	opts := config.Options{UpdateLock: scheduling.NewUpdateLock()}
	registerManualUpdateRoute(t.Context(), app, testAuthMiddleware(), opts)
	for _, query := range []string{"", "?container=", "?container=app&async=true", "?image=app"} {
		req := httptest.NewRequest(http.MethodPost, "/v1/manual-update"+query, nil)
		req.Header.Set("Authorization", "Bearer test")
		response, err := app.Test(req)
		require.NoError(t, err)
		assert.Equal(t, http.StatusBadRequest, response.StatusCode)
		response.Body.Close()
	}
	req := httptest.NewRequest(http.MethodPost, "/v1/manual-update?container=app", nil)
	response, err := app.Test(req)
	require.NoError(t, err)
	defer response.Body.Close()
	assert.Equal(t, http.StatusUnauthorized, response.StatusCode)
}
