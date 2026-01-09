package statusgo

import (
	"encoding/json"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/brianvoe/gofakeit/v6"

	"github.com/status-im/status-go/internal/db/multiaccounts"
	"github.com/status-im/status-go/internal/db/multiaccounts/settings"
	"github.com/status-im/status-go/signal"
)

func TestSetSignalHandler(t *testing.T) {
	// Setup
	var data string
	signal.SetHandler(func(b []byte) {
		data = string(b)
	})
	t.Cleanup(signal.ResetHandler)

	// Test data
	testAccount := &multiaccounts.Account{Name: "test"}
	testSettings := &settings.Settings{KeyUID: "0x1"}
	testEnsUsernames := json.RawMessage(`{"test": "test"}`)

	// Action
	signal.SendLoggedIn(testAccount, testSettings, testEnsUsernames, nil)

	// Assertions
	require.Contains(t, data, `"key-uid":"0x1"`, "Signal should contain the correct KeyUID")
	require.Contains(t, data, `"name":"test"`, "Signal should contain the correct account name")
	require.Contains(t, data, `"ensUsernames":{"test":"test"}`, "Signal should contain the correct ENS usernames")
}

func TestIntendedPanic(t *testing.T) {
	message := gofakeit.LetterN(5)
	require.PanicsWithError(t, message, func() {
		IntendedPanic(message)
	})
}
