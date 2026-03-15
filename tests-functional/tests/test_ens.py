import asyncio
import json
import logging
from contextlib import contextmanager
from uuid import uuid4

import pytest

from clients.api import ApiResponseError
import resources.constants as constants
from steps.async_messenger import AsyncMessengerSteps
from utils.config import Config

logger = logging.getLogger(__name__)

ANVIL_RPC_URL = "http://anvil:8545"
ENS_USERNAME = "testuser"
ENS_FULL_NAME = f"{ENS_USERNAME}.stateofus.eth"
CHAIN_ID = constants.ANVIL_NETWORK_ID


def cast_send(foundry, to, sig, args, private_key=constants.DEPLOYER_ACCOUNT.private_key):
    args_str = " ".join(str(a) for a in args)
    cmd = f"cast send {to} '{sig}' {args_str} --rpc-url {ANVIL_RPC_URL} --private-key {private_key}"
    result = foundry.container.exec_run(cmd)
    if result.exit_code != 0:
        raise RuntimeError(f"cast send failed: {result.output.decode().strip()}")
    return result.output.decode().strip()


def cast_call(foundry, to, sig, args=None):
    args_str = " ".join(str(a) for a in args) if args else ""
    cmd = f"cast call {to} '{sig}' {args_str} --rpc-url {ANVIL_RPC_URL}"
    result = foundry.container.exec_run(cmd)
    if result.exit_code != 0:
        raise RuntimeError(f"cast call failed: {result.output.decode().strip()}")
    return result.output.decode().strip()


def cast_calldata(foundry, sig, args):
    args_str = " ".join(str(a) for a in args)
    cmd = f"cast calldata '{sig}' {args_str}"
    result = foundry.container.exec_run(cmd)
    if result.exit_code != 0:
        raise RuntimeError(f"cast calldata failed: {result.output.decode().strip()}")
    return result.output.decode().strip()


def cast_keccak(foundry, text):
    cmd = f"cast keccak '{text}'"
    result = foundry.container.exec_run(cmd)
    if result.exit_code != 0:
        raise RuntimeError(f"cast keccak failed: {result.output.decode().strip()}")
    return result.output.decode().strip()


def extract_pubkey_coordinates(public_key):
    raw = public_key
    if raw.startswith("0x"):
        raw = raw[2:]
    if raw.startswith("04"):
        raw = raw[2:]
    x = "0x" + raw[:64]
    y = "0x" + raw[64:128]
    return x, y


def cast_rpc(foundry, method, params=None):
    params_str = " ".join(str(p) for p in params) if params else ""
    cmd = f"cast rpc {method} {params_str} --rpc-url {ANVIL_RPC_URL}"
    result = foundry.container.exec_run(cmd)
    if result.exit_code != 0:
        raise RuntimeError(f"cast rpc {method} failed: {result.output.decode().strip()}")
    return result.output.decode().strip()


def get_block_timestamp(foundry):
    cmd = f"cast block latest --field timestamp --rpc-url {ANVIL_RPC_URL}"
    result = foundry.container.exec_run(cmd)
    if result.exit_code != 0:
        raise RuntimeError(f"get_block_timestamp failed: {result.output.decode().strip()}")
    return int(result.output.decode().strip())


def sync_registry_to_well_known(foundry, registry_addr, username):
    well_known = "0x00000000000C2E074eC69A0dFb2997BA6C7d2e1e"
    user_namehash = f"$(cast namehash '{username}.stateofus.eth')"
    cmd = f"/app/sync_registry.sh {registry_addr} {well_known} {ANVIL_RPC_URL} {user_namehash}"
    result = foundry.container.exec_run(["sh", "-c", cmd])
    if result.exit_code != 0:
        raise RuntimeError(f"sync_registry failed: {result.output.decode().strip()}")


@contextmanager
def anvil_snapshot(foundry):
    """Snapshot Anvil state and revert on exit. Use around evm_increaseTime
    to prevent permanent timestamp drift from affecting other tests."""
    raw = cast_rpc(foundry, "evm_snapshot")
    snapshot_id = json.loads(raw)
    try:
        yield snapshot_id
    finally:
        cast_rpc(foundry, "evm_revert", [snapshot_id])


def register_ens_name(foundry, ens_addresses, username, account_address, public_key, private_key=constants.DEPLOYER_ACCOUNT.private_key):
    token = ens_addresses["token"]
    registrar = ens_addresses["registrar"]

    label = cast_keccak(foundry, username)
    x, y = extract_pubkey_coordinates(public_key)

    extra_data = cast_calldata(
        foundry,
        "register(bytes32,address,bytes32,bytes32)",
        [label, account_address, x, y],
    )

    price_raw = cast_call(foundry, registrar, "getPrice()(uint256)")
    price = price_raw.strip().split()[0]

    # Mint tokens to the registering account (only deployer/controller can mint)
    cast_send(
        foundry,
        token,
        "generateTokens(address,uint256)",
        [account_address, price],
    )

    # Register with the caller's key
    cast_send(
        foundry,
        token,
        "approveAndCall(address,uint256,bytes)",
        [registrar, price, extra_data],
        private_key=private_key,
    )


MAINNET_NETWORK = {
    "chainID": 1,
    "chainName": "Ethereum Mainnet",
    "rpcProviders": [
        {
            "chainId": 1,
            "name": "Anvil as Mainnet",
            "url": Config.anvil_url,
            "enabled": True,
            "authType": "token-auth",
            "enableRpsLimiter": False,
            "type": "embedded-direct",
        }
    ],
    "shortName": "eth",
    "nativeCurrencyName": "Ether",
    "nativeCurrencySymbol": "ETH",
    "nativeCurrencyDecimals": 18,
    "isTest": False,
    "layer": 1,
    "enabled": True,
    "isActive": True,
    "isDeactivatable": False,
}


@pytest.mark.rpc
@pytest.mark.ens
@pytest.mark.asyncio
class TestEnsVisibility(AsyncMessengerSteps):

    @pytest.fixture
    async def sender(self, async_backend_new_profile, foundry_client, ens_addresses, multicall3_deployer):
        backend = await async_backend_new_profile(
            "ens_sender",
            multicall_contract_address=multicall3_deployer.contract_address,
        )
        username = f"ensvis{uuid4().hex[:8]}"
        full_name = f"{username}.stateofus.eth"

        register_ens_name(
            foundry_client,
            ens_addresses,
            username,
            constants.DEPLOYER_ACCOUNT.address,
            backend.public_key,
        )
        sync_registry_to_well_known(foundry_client, ens_addresses["registry"], username)
        backend.backend.ens_service.add(CHAIN_ID, full_name)
        logger.info(f"Sender registered and linked {full_name}")

        backend._ens_full_name = full_name
        return backend

    @pytest.fixture
    async def receiver(self, async_backend_new_profile, ens_addresses, multicall3_deployer):
        return await async_backend_new_profile(
            "ens_receiver",
            multicall_contract_address=multicall3_deployer.contract_address,
            extra_networks_override=[MAINNET_NETWORK],
            verify_ens_contract_address=ens_addresses["registry"],
        )

    async def test_ens_name_visible_to_contact(self, sender, receiver):
        """Verify ENS name propagates to a contact via sendContactUpdates.

        Tests ContactUpdate protobuf propagation, not chat messages — Go's
        createChatIdentity hardcodes EnsName="" so regular messages don't
        carry the sender's ENS name yet.
        """
        full_name = sender._ens_full_name

        await self.make_contacts(sender, receiver)

        sender.wakuext_service.send_contact_updates(full_name, "", "blue")
        logger.info(f"Sender propagated ENS name: {full_name}")

        contact = None
        for _ in range(30):
            contact = receiver.wakuext_service.get_contact_by_id(sender.public_key)
            if contact and contact.get("name") == full_name:
                break
            await asyncio.sleep(1)

        assert contact is not None, "get_contact_by_id returned None"
        assert contact.get("name") == full_name, f"ENS name not visible to receiver: expected={full_name}, got={contact.get('name')}"

    async def test_ens_name_verified_by_contact(self, sender, receiver):
        """Verify ENS name can be marked as verified on the receiver side.

        Uses wakuext_ensVerified RPC (manual verification) rather than the
        automatic 30s verifier loop, because HandleContactUpdate does not call
        ensVerifier.Add() — so no verification record is created for the loop
        to process.
        """
        full_name = sender._ens_full_name

        await self.make_contacts(sender, receiver)

        sender.wakuext_service.send_contact_updates(full_name, "", "blue")
        logger.info(f"Sender propagated ENS name: {full_name}")

        # Wait for the ENS name to appear on the contact
        contact = None
        for _ in range(30):
            contact = receiver.wakuext_service.get_contact_by_id(sender.public_key)
            if contact and contact.get("name") == full_name:
                break
            await asyncio.sleep(1)

        assert contact is not None, "get_contact_by_id returned None"
        assert contact.get("name") == full_name, f"ENS name not visible: expected={full_name}, got={contact.get('name')}"

        # Trigger ENS verification on the receiver.
        # Note: automatic verification via the 30s verifier loop does not trigger here
        # because HandleContactUpdate does not call ensVerifier.Add() and
        # createChatIdentity hardcodes EnsName="" (protocol/messenger.go:1096 TODO).
        # We use wakuext_ensVerified to create the record and mark it verified.
        receiver.wakuext_service.ens_verified(sender.public_key, full_name)

        contact = receiver.wakuext_service.get_contact_by_id(sender.public_key)
        assert contact is not None, "get_contact_by_id returned None after verification"
        assert contact.get("ensVerified") is True, f"ENS name not verified after ensVerified call: contact={contact}"


@pytest.mark.rpc
@pytest.mark.ens
class TestEnsRegistration:

    @pytest.fixture(autouse=True)
    def setup(self, backend_new_profile, foundry_client, ens_addresses, multicall3_deployer):
        self.foundry = foundry_client
        self.ens_addresses = ens_addresses
        self.rpc_client = backend_new_profile(
            name="ens_user",
            multicall_contract_address=multicall3_deployer.contract_address,
        )

    def test_ens_register_and_verify(self):
        public_key = self.rpc_client.public_key
        assert public_key, "Backend public key not available"

        price = self.rpc_client.ens_service.price(CHAIN_ID)
        assert price, "ens_price returned empty"
        logger.info(f"ENS registration price: {price}")

        registrar_addr = self.rpc_client.ens_service.get_registrar_address(CHAIN_ID)
        assert registrar_addr, "ens_getRegistrarAddress returned empty"
        assert (
            registrar_addr == self.ens_addresses["registrar"]
        ), f"Registrar mismatch: RPC={registrar_addr}, deployed={self.ens_addresses['registrar']}"

        register_ens_name(
            self.foundry,
            self.ens_addresses,
            ENS_USERNAME,
            constants.DEPLOYER_ACCOUNT.address,
            public_key,
        )
        logger.info(f"Registered {ENS_FULL_NAME} on-chain")

        sync_registry_to_well_known(self.foundry, self.ens_addresses["registry"], ENS_USERNAME)

        owner = self.rpc_client.ens_service.owner_of(CHAIN_ID, ENS_FULL_NAME)
        assert (
            owner.lower() == constants.DEPLOYER_ACCOUNT.address.lower()
        ), f"Owner mismatch: expected={constants.DEPLOYER_ACCOUNT.address}, got={owner}"

        resolved_pubkey = self.rpc_client.ens_service.public_key_of(CHAIN_ID, ENS_FULL_NAME)
        assert resolved_pubkey, "ens_publicKeyOf returned empty"
        assert resolved_pubkey.startswith("0x04"), f"Unexpected pubkey format: {resolved_pubkey}"

        resolver_addr = self.rpc_client.ens_service.resolver(CHAIN_ID, ENS_FULL_NAME)
        assert (
            resolver_addr.lower() == self.ens_addresses["resolver"].lower()
        ), f"Resolver mismatch: expected={self.ens_addresses['resolver']}, got={resolver_addr}"

        resolved_addr = self.rpc_client.ens_service.address_of(CHAIN_ID, ENS_FULL_NAME)
        assert resolved_addr, "ens_addressOf returned empty"

        self.rpc_client.ens_service.add(CHAIN_ID, ENS_FULL_NAME)

        usernames = self.rpc_client.ens_service.get_ens_usernames()
        assert usernames, "ens_getEnsUsernames returned empty"
        found = any(u.get("username") == ENS_FULL_NAME for u in usernames)
        assert found, f"{ENS_FULL_NAME} not found in {usernames}"

    def test_ens_link_and_manage_names(self):
        public_key = self.rpc_client.public_key
        user1 = "linkuser1"
        user1_full = f"{user1}.stateofus.eth"
        user2 = "linkuser2"
        user2_full = f"{user2}.stateofus.eth"

        register_ens_name(
            self.foundry,
            self.ens_addresses,
            user1,
            constants.DEPLOYER_ACCOUNT.address,
            public_key,
        )
        sync_registry_to_well_known(self.foundry, self.ens_addresses["registry"], user1)
        self.rpc_client.ens_service.add(CHAIN_ID, user1_full)
        logger.info(f"Linked {user1_full}")

        usernames = self.rpc_client.ens_service.get_ens_usernames()
        assert any(u.get("username") == user1_full for u in usernames), f"{user1_full} not found in {usernames}"

        resolver_addr = self.rpc_client.ens_service.resolver(CHAIN_ID, user1_full)
        assert resolver_addr, "ens_resolver returned empty"
        logger.info(f"Resolver for {user1_full}: {resolver_addr}")

        register_ens_name(
            self.foundry,
            self.ens_addresses,
            user2,
            constants.DEPLOYER_ACCOUNT.address,
            public_key,
        )
        sync_registry_to_well_known(self.foundry, self.ens_addresses["registry"], user2)
        self.rpc_client.ens_service.add(CHAIN_ID, user2_full)
        logger.info(f"Linked {user2_full}")

        usernames = self.rpc_client.ens_service.get_ens_usernames()
        names = [u.get("username") for u in usernames]
        assert user1_full in names, f"{user1_full} not found in {names}"
        assert user2_full in names, f"{user2_full} not found in {names}"

        self.rpc_client.ens_service.remove(CHAIN_ID, user1_full)
        logger.info(f"Removed {user1_full}")

        usernames = self.rpc_client.ens_service.get_ens_usernames()
        names = [u.get("username") for u in usernames if not u.get("removed")]
        assert user1_full not in names, f"{user1_full} should have been removed"
        assert user2_full in names, f"{user2_full} not found after removal of first"

        with pytest.raises(ApiResponseError):
            self.rpc_client.ens_service.add(CHAIN_ID, "INVALID")

        with pytest.raises(ApiResponseError):
            self.rpc_client.ens_service.add(CHAIN_ID, "test.stateofus.eth.stateofus.eth")

    def test_ens_validity_time(self):
        public_key = self.rpc_client.public_key
        username = "timeuser"
        full_name = f"{username}.stateofus.eth"
        one_year_seconds = 365 * 24 * 60 * 60

        register_ens_name(
            self.foundry,
            self.ens_addresses,
            username,
            constants.DEPLOYER_ACCOUNT.address,
            public_key,
        )
        sync_registry_to_well_known(self.foundry, self.ens_addresses["registry"], username)
        logger.info(f"Registered {full_name}")

        # expire_at expects plain username, not full ENS name
        expire_hex = self.rpc_client.ens_service.expire_at(CHAIN_ID, username)
        assert expire_hex, "ens_expireAt returned empty"
        expire_time = int(expire_hex, 16)
        logger.info(f"Expiration timestamp: {expire_time}")

        current_time = get_block_timestamp(self.foundry)
        expected_expire = current_time + one_year_seconds
        delta = abs(expire_time - expected_expire)
        assert delta < 60, f"Expiration {expire_time} too far from expected {expected_expire} (delta={delta}s)"

        with anvil_snapshot(self.foundry):
            half_year_seconds = 180 * 24 * 60 * 60
            cast_rpc(self.foundry, "evm_increaseTime", [half_year_seconds])
            cast_rpc(self.foundry, "evm_mine")
            logger.info("Advanced time by 180 days")

            expire_hex_after = self.rpc_client.ens_service.expire_at(CHAIN_ID, username)
            expire_time_after = int(expire_hex_after, 16)
            assert expire_time_after == expire_time, f"Expiration changed after time advance: was {expire_time}, now {expire_time_after}"

    def test_ens_release(self):
        public_key = self.rpc_client.public_key
        username = "releaseuser"
        full_name = f"{username}.stateofus.eth"
        registrar = self.ens_addresses["registrar"]
        one_year_seconds = 365 * 24 * 60 * 60

        register_ens_name(
            self.foundry,
            self.ens_addresses,
            username,
            constants.DEPLOYER_ACCOUNT.address,
            public_key,
        )
        sync_registry_to_well_known(self.foundry, self.ens_addresses["registry"], username)
        logger.info(f"Registered {full_name}")

        self.rpc_client.ens_service.add(CHAIN_ID, full_name)
        usernames = self.rpc_client.ens_service.get_ens_usernames()
        assert any(u.get("username") == full_name for u in usernames), f"{full_name} not found in {usernames}"

        label = cast_keccak(self.foundry, username)
        with pytest.raises(RuntimeError):
            cast_send(self.foundry, registrar, "release(bytes32)", [label])
        logger.info("Early release correctly reverted")

        with anvil_snapshot(self.foundry):
            cast_rpc(self.foundry, "evm_increaseTime", [one_year_seconds + 1])
            cast_rpc(self.foundry, "evm_mine")
            logger.info("Advanced time past 365 days")

            cast_send(self.foundry, registrar, "release(bytes32)", [label])
            logger.info(f"Released {full_name}")

            sync_registry_to_well_known(self.foundry, self.ens_addresses["registry"], username)

            owner = self.rpc_client.ens_service.owner_of(CHAIN_ID, full_name)
            assert owner == "0x0000000000000000000000000000000000000000", f"Owner should be zero after release: {owner}"

        self.rpc_client.ens_service.remove(CHAIN_ID, full_name)

        usernames = self.rpc_client.ens_service.get_ens_usernames() or []
        active = [u.get("username") for u in usernames if not u.get("removed")]
        assert full_name not in active, f"{full_name} should have been removed"
