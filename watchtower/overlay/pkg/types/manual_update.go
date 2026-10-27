package types

import "context"

type manualUpdateKey struct{}

// WithManualUpdate marks only this request; shared Docker client options stay unchanged.
func WithManualUpdate(ctx context.Context) context.Context {
	return context.WithValue(ctx, manualUpdateKey{}, true)
}

func IsManualUpdate(ctx context.Context) bool {
	manual, _ := ctx.Value(manualUpdateKey{}).(bool)
	return manual
}
