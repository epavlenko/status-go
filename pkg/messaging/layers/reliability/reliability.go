package reliability

import (
	"crypto/ecdsa"
	"sync"

	"github.com/golang/protobuf/proto"
	"github.com/pkg/errors"
	"go.uber.org/zap"

	"github.com/waku-org/sds-go-bindings/sds"

	mvdsnode "github.com/status-im/mvds/node"
	mvdsproto "github.com/status-im/mvds/protobuf"
	mvdsstate "github.com/status-im/mvds/state"

	datasync2 "github.com/status-im/status-go/pkg/messaging/layers/reliability/datasync"
	datasyncpeer "github.com/status-im/status-go/pkg/messaging/layers/reliability/datasync/peer"
)

// MessageDispatcher is a function that dispatches messages to a given public key.
//
//	publicKey: the recipient public key
//	wrappedPayload: the datasync wrapped payload
//	messages: the original messages that were wrapped
type MessageDispatcher func(publicKey *ecdsa.PublicKey, wrappedPayload []byte, messages [][]byte) error

// MissingDependenciesHandler is triggered when SDS reports missing dependencies
// for an incoming message.
type MissingDependenciesHandler func(messageID string, missingDeps []string, channelID string) error

type Reliability struct {
	identity              *ecdsa.PrivateKey
	datasync              *datasync2.DataSync
	mvdsPersistence       mvdsnode.Persistence
	mvdsStatusChangeEvent chan mvdsnode.PeerStatusChangeEvent
	sdsManager            *sds.ReliabilityManager
	logger                *zap.Logger

	missingDepsHandlerMu sync.RWMutex
	missingDepsHandler   MissingDependenciesHandler
}

func NewReliability(datasyncPersistence mvdsnode.Persistence, identity *ecdsa.PrivateKey, logger *zap.Logger) *Reliability {
	logger = logger.Named("reliability")
	r := &Reliability{
		identity:              identity,
		mvdsPersistence:       datasyncPersistence,
		mvdsStatusChangeEvent: make(chan mvdsnode.PeerStatusChangeEvent, 5),
		logger:                logger,
	}

	r.sdsManager = newSdsReliabilityManager(logger.Named("sds"), r.handleMissingDependencies)

	return r
}

// SetMissingDependenciesHandler configures how SDS missing dependencies should be handled.
func (r *Reliability) SetMissingDependenciesHandler(handler MissingDependenciesHandler) {
	r.missingDepsHandlerMu.Lock()
	defer r.missingDepsHandlerMu.Unlock()
	r.missingDepsHandler = handler
}

func (r *Reliability) handleMissingDependencies(messageID sds.MessageID, missingDeps []sds.MessageID, channelID string) {
	r.missingDepsHandlerMu.RLock()
	handler := r.missingDepsHandler
	r.missingDepsHandlerMu.RUnlock()

	if handler == nil || len(missingDeps) == 0 {
		return
	}

	missingDepsAsString := make([]string, len(missingDeps))
	for i, dep := range missingDeps {
		missingDepsAsString[i] = string(dep)
	}

	if err := handler(string(messageID), missingDepsAsString, channelID); err != nil {
		r.logger.Debug("failed to fetch missing dependencies from sds callback", zap.Error(err))
	}
}

func (r *Reliability) Start(dispatch MessageDispatcher) error {
	dataSyncTransport := datasync2.NewNodeTransport()
	dataSyncNode, err := mvdsnode.NewPersistentNode(
		r.mvdsPersistence,
		dataSyncTransport,
		datasyncpeer.PublicKeyToPeerID(r.identity.PublicKey),
		mvdsnode.BATCH,
		datasync2.CalculateSendTime,
		r.mvdsStatusChangeEvent,
		r.logger,
	)
	if err != nil {
		return err
	}

	r.datasync = datasync2.New(dataSyncNode, dataSyncTransport, true, r.logger)

	mvdsDispatch := func(receiver mvdsstate.PeerID, payload *mvdsproto.Payload) error {
		if !payload.IsValid() {
			return errors.New("payload is invalid")
		}

		marshalledPayload, err := proto.Marshal(payload)
		if err != nil {
			return errors.Wrap(err, "failed to marshal payload")
		}

		publicKey, err := datasyncpeer.IDToPublicKey(receiver)
		if err != nil {
			return errors.Wrap(err, "failed to convert id to public key")
		}

		messages := make([][]byte, 0, len(payload.Messages))
		for _, msg := range payload.Messages {
			messages = append(messages, msg.Body)
		}

		return dispatch(publicKey, marshalledPayload, messages)
	}

	r.datasync.Init(mvdsDispatch, r.logger)
	r.datasync.Start(datasync2.DatasyncTicker)

	return nil
}

func (r *Reliability) Stop() {
	if r.Started() {
		r.datasync.Stop()
	}
	r.datasync = nil
	if r.sdsManager != nil {
		err := r.sdsManager.Cleanup()
		if err != nil {
			r.logger.Error("failed to cleanup sds reliability manager", zap.Error(err))
		}
		r.sdsManager = nil
	}
}

func (r *Reliability) Started() bool {
	return r.datasync != nil
}

// WrapAndQueueMessageForDispatch wraps the message in the reliability layer,
// then queues it for delivery to the target public key using configured MVDSDispatcher.
func (r *Reliability) WrapAndQueueMessageForDispatch(publicKey *ecdsa.PublicKey, message []byte) (mvdsstate.MessageID, error) {
	groupID := datasync2.ToOneToOneGroupID(&r.identity.PublicKey, publicKey)
	peerID := datasyncpeer.PublicKeyToPeerID(*publicKey)
	exist, err := r.datasync.IsPeerInGroup(groupID, peerID)
	if err != nil {
		return mvdsstate.MessageID{}, errors.Wrap(err, "failed to check if peer is in group")
	}
	if !exist {
		if err := r.datasync.AddPeer(groupID, peerID); err != nil {
			return mvdsstate.MessageID{}, errors.Wrap(err, "failed to add peer")
		}
	}
	return r.datasync.AppendMessage(groupID, message)
}

// UnwrapAndAcknowledge tries to unwrap received datasync message,
// and potentially acknowledges it.
func (r *Reliability) UnwrapAndAcknowledgeMessage(publicKey *ecdsa.PublicKey, message []byte) (*mvdsproto.Payload, error) {
	return r.datasync.Unwrap(publicKey, message)
}

// ReportPeerOnline reports to MVDS that a peer is online at a given event time.
func (r *Reliability) ReportPeerOnline(publicKey *ecdsa.PublicKey, eventTime uint64) {
	select {
	case r.mvdsStatusChangeEvent <- mvdsnode.PeerStatusChangeEvent{
		PeerID:    datasyncpeer.PublicKeyToPeerID(*publicKey),
		Status:    mvdsnode.OnlineStatus,
		EventTime: eventTime,
	}:
	default:
		r.logger.Debug("mvdsStatusChangeEvent channel is full")
	}
}
