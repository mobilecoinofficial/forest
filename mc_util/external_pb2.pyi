"""
@generated by mypy-protobuf.  Do not edit manually!
isort:skip_file
"""
import builtins
import google.protobuf.descriptor
import google.protobuf.internal.containers
import google.protobuf.message
import typing
import typing_extensions

DESCRIPTOR: google.protobuf.descriptor.FileDescriptor = ...

class RistrettoPrivate(google.protobuf.message.Message):
    """/////////////////////////////////////////////////////////////////////////////
    `keys` crate
    /////////////////////////////////////////////////////////////////////////////

    / A Ristretto private key.
    """

    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    DATA_FIELD_NUMBER: builtins.int
    data: builtins.bytes = ...
    def __init__(
        self,
        *,
        data: builtins.bytes = ...,
    ) -> None: ...
    def ClearField(
        self, field_name: typing_extensions.Literal["data", b"data"]
    ) -> None: ...

global___RistrettoPrivate = RistrettoPrivate

class CompressedRistretto(google.protobuf.message.Message):
    """/ A 32-byte compressed Ristretto curve point (public key)"""

    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    DATA_FIELD_NUMBER: builtins.int
    data: builtins.bytes = ...
    def __init__(
        self,
        *,
        data: builtins.bytes = ...,
    ) -> None: ...
    def ClearField(
        self, field_name: typing_extensions.Literal["data", b"data"]
    ) -> None: ...

global___CompressedRistretto = CompressedRistretto

class Ed25519Public(google.protobuf.message.Message):
    """/ An Ed25519 public key, for validating signatures."""

    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    DATA_FIELD_NUMBER: builtins.int
    data: builtins.bytes = ...
    def __init__(
        self,
        *,
        data: builtins.bytes = ...,
    ) -> None: ...
    def ClearField(
        self, field_name: typing_extensions.Literal["data", b"data"]
    ) -> None: ...

global___Ed25519Public = Ed25519Public

class Ed25519Signature(google.protobuf.message.Message):
    """/ An Ed25519 signature object"""

    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    DATA_FIELD_NUMBER: builtins.int
    data: builtins.bytes = ...
    def __init__(
        self,
        *,
        data: builtins.bytes = ...,
    ) -> None: ...
    def ClearField(
        self, field_name: typing_extensions.Literal["data", b"data"]
    ) -> None: ...

global___Ed25519Signature = Ed25519Signature

class AccountKey(google.protobuf.message.Message):
    """/////////////////////////////////////////////////////////////////////////////
    `account-keys` crate
    /////////////////////////////////////////////////////////////////////////////

    / Complete AccountKey, containing the pair of secret keys, which can be used
    / for spending, and optionally some Fog related info that is used to form
    / public addresses for accounts that sign up with Fog service.
    /
    / This matches the Rust `transaction::AccountKey` struct.
    """

    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    VIEW_PRIVATE_KEY_FIELD_NUMBER: builtins.int
    SPEND_PRIVATE_KEY_FIELD_NUMBER: builtins.int
    FOG_REPORT_URL_FIELD_NUMBER: builtins.int
    FOG_REPORT_ID_FIELD_NUMBER: builtins.int
    FOG_AUTHORITY_SPKI_FIELD_NUMBER: builtins.int
    @property
    def view_private_key(self) -> global___RistrettoPrivate:
        """/ Private key 'a' used for view-key matching."""
        pass
    @property
    def spend_private_key(self) -> global___RistrettoPrivate:
        """/ Private key `b` used for spending."""
        pass
    fog_report_url: typing.Text = ...
    """/ Optional url of fog report server.
    / Empty string when not in use, i.e. for accounts that don't have fog service.
    """

    fog_report_id: typing.Text = ...
    """/ Optional fog report id.
    / The fog report server may serve multiple reports, this id disambiguates
    / which one to use when sending to this account.
    """

    fog_authority_spki: builtins.bytes = ...
    """/ Optional fog authority subjectPublicKeyInfo.
    / Empty when not in use.
    """
    def __init__(
        self,
        *,
        view_private_key: typing.Optional[global___RistrettoPrivate] = ...,
        spend_private_key: typing.Optional[global___RistrettoPrivate] = ...,
        fog_report_url: typing.Text = ...,
        fog_report_id: typing.Text = ...,
        fog_authority_spki: builtins.bytes = ...,
    ) -> None: ...
    def HasField(
        self,
        field_name: typing_extensions.Literal[
            "spend_private_key",
            b"spend_private_key",
            "view_private_key",
            b"view_private_key",
        ],
    ) -> builtins.bool: ...
    def ClearField(
        self,
        field_name: typing_extensions.Literal[
            "fog_authority_spki",
            b"fog_authority_spki",
            "fog_report_id",
            b"fog_report_id",
            "fog_report_url",
            b"fog_report_url",
            "spend_private_key",
            b"spend_private_key",
            "view_private_key",
            b"view_private_key",
        ],
    ) -> None: ...

global___AccountKey = AccountKey

class PublicAddress(google.protobuf.message.Message):
    """/ A public address, used to identify recipients."""

    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    VIEW_PUBLIC_KEY_FIELD_NUMBER: builtins.int
    SPEND_PUBLIC_KEY_FIELD_NUMBER: builtins.int
    FOG_REPORT_URL_FIELD_NUMBER: builtins.int
    FOG_REPORT_ID_FIELD_NUMBER: builtins.int
    FOG_AUTHORITY_SIG_FIELD_NUMBER: builtins.int
    @property
    def view_public_key(self) -> global___CompressedRistretto:
        """/ View public key"""
        pass
    @property
    def spend_public_key(self) -> global___CompressedRistretto:
        """/ Spend public key"""
        pass
    fog_report_url: typing.Text = ...
    """/ Optional url of fog report server.
    / Empty string when not in use, i.e. for accounts that don't have fog service.
    / Indicates the place at which the fog report server should be contacted.
    """

    fog_report_id: typing.Text = ...
    """/ Optional fog report id.
    / The fog report server may serve multiple reports, this id disambiguates
    / which one to use when sending to this account.
    """

    fog_authority_sig: builtins.bytes = ...
    """/ View key signature over the fog authority subjectPublicKeyInfo.
    /
    / This must be parseable as a RistrettoSignature.
    """
    def __init__(
        self,
        *,
        view_public_key: typing.Optional[global___CompressedRistretto] = ...,
        spend_public_key: typing.Optional[global___CompressedRistretto] = ...,
        fog_report_url: typing.Text = ...,
        fog_report_id: typing.Text = ...,
        fog_authority_sig: builtins.bytes = ...,
    ) -> None: ...
    def HasField(
        self,
        field_name: typing_extensions.Literal[
            "spend_public_key",
            b"spend_public_key",
            "view_public_key",
            b"view_public_key",
        ],
    ) -> builtins.bool: ...
    def ClearField(
        self,
        field_name: typing_extensions.Literal[
            "fog_authority_sig",
            b"fog_authority_sig",
            "fog_report_id",
            b"fog_report_id",
            "fog_report_url",
            b"fog_report_url",
            "spend_public_key",
            b"spend_public_key",
            "view_public_key",
            b"view_public_key",
        ],
    ) -> None: ...

global___PublicAddress = PublicAddress

class RootIdentity(google.protobuf.message.Message):
    """/ A KDF can be used to stretch a 32 byte secret into multiple secret private keys.
    / The RootIdentity is a compact form of a user's account key, if it has been
    / derived in this way. This may be useful for e.g. paper wallets.
    """

    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    ROOT_ENTROPY_FIELD_NUMBER: builtins.int
    FOG_REPORT_URL_FIELD_NUMBER: builtins.int
    FOG_REPORT_ID_FIELD_NUMBER: builtins.int
    FOG_AUTHORITY_SPKI_FIELD_NUMBER: builtins.int
    @property
    def root_entropy(self) -> global___RootEntropy:
        """/ The root entropy used to derive cryptonote private keys for this account"""
        pass
    fog_report_url: typing.Text = ...
    """/ Optional url of fog report server, same as in AccountKey"""

    fog_report_id: typing.Text = ...
    """/ Optional fog report id, same as in AccountKey"""

    fog_authority_spki: builtins.bytes = ...
    """/ Optional fog authority subjectPublicKeyInfo.
    / Empty when not in use.
    """
    def __init__(
        self,
        *,
        root_entropy: typing.Optional[global___RootEntropy] = ...,
        fog_report_url: typing.Text = ...,
        fog_report_id: typing.Text = ...,
        fog_authority_spki: builtins.bytes = ...,
    ) -> None: ...
    def HasField(
        self, field_name: typing_extensions.Literal["root_entropy", b"root_entropy"]
    ) -> builtins.bool: ...
    def ClearField(
        self,
        field_name: typing_extensions.Literal[
            "fog_authority_spki",
            b"fog_authority_spki",
            "fog_report_id",
            b"fog_report_id",
            "fog_report_url",
            b"fog_report_url",
            "root_entropy",
            b"root_entropy",
        ],
    ) -> None: ...

global___RootIdentity = RootIdentity

class RootEntropy(google.protobuf.message.Message):
    """/ A 32 byte secret used as input key material to derive private keys"""

    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    DATA_FIELD_NUMBER: builtins.int
    data: builtins.bytes = ...
    def __init__(
        self,
        *,
        data: builtins.bytes = ...,
    ) -> None: ...
    def ClearField(
        self, field_name: typing_extensions.Literal["data", b"data"]
    ) -> None: ...

global___RootEntropy = RootEntropy

class ViewKey(google.protobuf.message.Message):
    """/ A ViewKey is a reduced AccountKey -- it contains the private key necessary to
    / view your transactions and see the amounts, but not to send new transactions.
    / This concept is part of Cryptonote.
    / In Mobilecoin, all public addresses correspond to subaddresses, and often
    / the "default subaddress" is used.
    / The ViewKey similarly corresponds to a particular subaddress.
    """

    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    VIEW_PRIVATE_KEY_FIELD_NUMBER: builtins.int
    SPEND_PUBLIC_KEY_FIELD_NUMBER: builtins.int
    @property
    def view_private_key(self) -> global___RistrettoPrivate:
        """/ The view-private-key of the account. This enables to check if a transaction
        / corresponds to this subaddress, and to interact with fog.
        """
        pass
    @property
    def spend_public_key(self) -> global___CompressedRistretto:
        """/ The spend public key of the account.
        / This value also appears in the public address.
        """
        pass
    def __init__(
        self,
        *,
        view_private_key: typing.Optional[global___RistrettoPrivate] = ...,
        spend_public_key: typing.Optional[global___CompressedRistretto] = ...,
    ) -> None: ...
    def HasField(
        self,
        field_name: typing_extensions.Literal[
            "spend_public_key",
            b"spend_public_key",
            "view_private_key",
            b"view_private_key",
        ],
    ) -> builtins.bool: ...
    def ClearField(
        self,
        field_name: typing_extensions.Literal[
            "spend_public_key",
            b"spend_public_key",
            "view_private_key",
            b"view_private_key",
        ],
    ) -> None: ...

global___ViewKey = ViewKey

class CurveScalar(google.protobuf.message.Message):
    """/////////////////////////////////////////////////////////////////////////////
    `trasaction/core` crate
    /////////////////////////////////////////////////////////////////////////////

    / A 32-byte scalar associated to the ristretto group.
    / This is the same as RistrettoPrivate, but they are used in different places.
    / TODO: MC-1605 Consider to factor out this type, or just this proto message.
    """

    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    DATA_FIELD_NUMBER: builtins.int
    data: builtins.bytes = ...
    def __init__(
        self,
        *,
        data: builtins.bytes = ...,
    ) -> None: ...
    def ClearField(
        self, field_name: typing_extensions.Literal["data", b"data"]
    ) -> None: ...

global___CurveScalar = CurveScalar

class KeyImage(google.protobuf.message.Message):
    """/ A 32-byte mobilecoin transaction key image."""

    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    DATA_FIELD_NUMBER: builtins.int
    data: builtins.bytes = ...
    def __init__(
        self,
        *,
        data: builtins.bytes = ...,
    ) -> None: ...
    def ClearField(
        self, field_name: typing_extensions.Literal["data", b"data"]
    ) -> None: ...

global___KeyImage = KeyImage

class Range(google.protobuf.message.Message):
    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    FROM_FIELD_NUMBER: builtins.int
    TO_FIELD_NUMBER: builtins.int
    to: builtins.int = ...
    def __init__(
        self,
        *,
        to: builtins.int = ...,
    ) -> None: ...
    def ClearField(
        self, field_name: typing_extensions.Literal["from", b"from", "to", b"to"]
    ) -> None: ...

global___Range = Range

class TxOutMembershipHash(google.protobuf.message.Message):
    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    DATA_FIELD_NUMBER: builtins.int
    data: builtins.bytes = ...
    def __init__(
        self,
        *,
        data: builtins.bytes = ...,
    ) -> None: ...
    def ClearField(
        self, field_name: typing_extensions.Literal["data", b"data"]
    ) -> None: ...

global___TxOutMembershipHash = TxOutMembershipHash

class TxOutMembershipElement(google.protobuf.message.Message):
    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    RANGE_FIELD_NUMBER: builtins.int
    HASH_FIELD_NUMBER: builtins.int
    @property
    def range(self) -> global___Range: ...
    @property
    def hash(self) -> global___TxOutMembershipHash: ...
    def __init__(
        self,
        *,
        range: typing.Optional[global___Range] = ...,
        hash: typing.Optional[global___TxOutMembershipHash] = ...,
    ) -> None: ...
    def HasField(
        self, field_name: typing_extensions.Literal["hash", b"hash", "range", b"range"]
    ) -> builtins.bool: ...
    def ClearField(
        self, field_name: typing_extensions.Literal["hash", b"hash", "range", b"range"]
    ) -> None: ...

global___TxOutMembershipElement = TxOutMembershipElement

class TxOutMembershipProof(google.protobuf.message.Message):
    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    INDEX_FIELD_NUMBER: builtins.int
    HIGHEST_INDEX_FIELD_NUMBER: builtins.int
    ELEMENTS_FIELD_NUMBER: builtins.int
    index: builtins.int = ...
    highest_index: builtins.int = ...
    @property
    def elements(
        self,
    ) -> google.protobuf.internal.containers.RepeatedCompositeFieldContainer[
        global___TxOutMembershipElement
    ]: ...
    def __init__(
        self,
        *,
        index: builtins.int = ...,
        highest_index: builtins.int = ...,
        elements: typing.Optional[
            typing.Iterable[global___TxOutMembershipElement]
        ] = ...,
    ) -> None: ...
    def ClearField(
        self,
        field_name: typing_extensions.Literal[
            "elements",
            b"elements",
            "highest_index",
            b"highest_index",
            "index",
            b"index",
        ],
    ) -> None: ...

global___TxOutMembershipProof = TxOutMembershipProof

class TxOutConfirmationNumber(google.protobuf.message.Message):
    """A hash of the shared secret of a transaction output.

    Can be used by the recipient of a transaction output to verify that the
    bearer of this number knew the shared secret of the transaction output,
    thereby providing evidence that they are the sender.
    """

    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    HASH_FIELD_NUMBER: builtins.int
    hash: builtins.bytes = ...
    def __init__(
        self,
        *,
        hash: builtins.bytes = ...,
    ) -> None: ...
    def ClearField(
        self, field_name: typing_extensions.Literal["hash", b"hash"]
    ) -> None: ...

global___TxOutConfirmationNumber = TxOutConfirmationNumber

class Amount(google.protobuf.message.Message):
    """Amount."""

    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    COMMITMENT_FIELD_NUMBER: builtins.int
    MASKED_VALUE_FIELD_NUMBER: builtins.int
    @property
    def commitment(self) -> global___CompressedRistretto:
        """A Pedersen commitment `v*G + s*H`"""
        pass
    masked_value: builtins.int = ...
    """`masked_value = value XOR_8 Blake2B("value_mask" || shared_secret)`"""
    def __init__(
        self,
        *,
        commitment: typing.Optional[global___CompressedRistretto] = ...,
        masked_value: builtins.int = ...,
    ) -> None: ...
    def HasField(
        self, field_name: typing_extensions.Literal["commitment", b"commitment"]
    ) -> builtins.bool: ...
    def ClearField(
        self,
        field_name: typing_extensions.Literal[
            "commitment", b"commitment", "masked_value", b"masked_value"
        ],
    ) -> None: ...

global___Amount = Amount

class EncryptedFogHint(google.protobuf.message.Message):
    """The bytes of encrypted fog hint"""

    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    DATA_FIELD_NUMBER: builtins.int
    data: builtins.bytes = ...
    def __init__(
        self,
        *,
        data: builtins.bytes = ...,
    ) -> None: ...
    def ClearField(
        self, field_name: typing_extensions.Literal["data", b"data"]
    ) -> None: ...

global___EncryptedFogHint = EncryptedFogHint

class EncryptedMemo(google.protobuf.message.Message):
    """The bytes of encrypted memo"""

    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    DATA_FIELD_NUMBER: builtins.int
    data: builtins.bytes = ...
    def __init__(
        self,
        *,
        data: builtins.bytes = ...,
    ) -> None: ...
    def ClearField(
        self, field_name: typing_extensions.Literal["data", b"data"]
    ) -> None: ...

global___EncryptedMemo = EncryptedMemo

class TxOut(google.protobuf.message.Message):
    """A Transaction Output."""

    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    AMOUNT_FIELD_NUMBER: builtins.int
    TARGET_KEY_FIELD_NUMBER: builtins.int
    PUBLIC_KEY_FIELD_NUMBER: builtins.int
    E_FOG_HINT_FIELD_NUMBER: builtins.int
    E_MEMO_FIELD_NUMBER: builtins.int
    @property
    def amount(self) -> global___Amount:
        """Amount."""
        pass
    @property
    def target_key(self) -> global___CompressedRistretto:
        """Public key."""
        pass
    @property
    def public_key(self) -> global___CompressedRistretto:
        """Public key."""
        pass
    @property
    def e_fog_hint(self) -> global___EncryptedFogHint:
        """Encrypted fog hint payload.
        This is an mc-crypto-box cryptogram for the fog ingest server,
        or a random cryptogram indistinguishable from a real one.
        """
        pass
    @property
    def e_memo(self) -> global___EncryptedMemo:
        """Encrypted memo"""
        pass
    def __init__(
        self,
        *,
        amount: typing.Optional[global___Amount] = ...,
        target_key: typing.Optional[global___CompressedRistretto] = ...,
        public_key: typing.Optional[global___CompressedRistretto] = ...,
        e_fog_hint: typing.Optional[global___EncryptedFogHint] = ...,
        e_memo: typing.Optional[global___EncryptedMemo] = ...,
    ) -> None: ...
    def HasField(
        self,
        field_name: typing_extensions.Literal[
            "amount",
            b"amount",
            "e_fog_hint",
            b"e_fog_hint",
            "e_memo",
            b"e_memo",
            "public_key",
            b"public_key",
            "target_key",
            b"target_key",
        ],
    ) -> builtins.bool: ...
    def ClearField(
        self,
        field_name: typing_extensions.Literal[
            "amount",
            b"amount",
            "e_fog_hint",
            b"e_fog_hint",
            "e_memo",
            b"e_memo",
            "public_key",
            b"public_key",
            "target_key",
            b"target_key",
        ],
    ) -> None: ...

global___TxOut = TxOut

class TxIn(google.protobuf.message.Message):
    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    RING_FIELD_NUMBER: builtins.int
    PROOFS_FIELD_NUMBER: builtins.int
    @property
    def ring(
        self,
    ) -> google.protobuf.internal.containers.RepeatedCompositeFieldContainer[
        global___TxOut
    ]:
        """ "Ring" of inputs, one of which is actually being spent."""
        pass
    @property
    def proofs(
        self,
    ) -> google.protobuf.internal.containers.RepeatedCompositeFieldContainer[
        global___TxOutMembershipProof
    ]:
        """Proof that each TxOut in `ring` is in the ledger."""
        pass
    def __init__(
        self,
        *,
        ring: typing.Optional[typing.Iterable[global___TxOut]] = ...,
        proofs: typing.Optional[typing.Iterable[global___TxOutMembershipProof]] = ...,
    ) -> None: ...
    def ClearField(
        self,
        field_name: typing_extensions.Literal["proofs", b"proofs", "ring", b"ring"],
    ) -> None: ...

global___TxIn = TxIn

class TxPrefix(google.protobuf.message.Message):
    """A transaction that a client submits to consensus"""

    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    INPUTS_FIELD_NUMBER: builtins.int
    OUTPUTS_FIELD_NUMBER: builtins.int
    FEE_FIELD_NUMBER: builtins.int
    TOMBSTONE_BLOCK_FIELD_NUMBER: builtins.int
    @property
    def inputs(
        self,
    ) -> google.protobuf.internal.containers.RepeatedCompositeFieldContainer[
        global___TxIn
    ]:
        """Transaction inputs."""
        pass
    @property
    def outputs(
        self,
    ) -> google.protobuf.internal.containers.RepeatedCompositeFieldContainer[
        global___TxOut
    ]:
        """Transaction outputs."""
        pass
    fee: builtins.int = ...
    """Fee paid to the foundation for this transaction"""

    tombstone_block: builtins.int = ...
    """The block index at which this transaction is no longer valid."""
    def __init__(
        self,
        *,
        inputs: typing.Optional[typing.Iterable[global___TxIn]] = ...,
        outputs: typing.Optional[typing.Iterable[global___TxOut]] = ...,
        fee: builtins.int = ...,
        tombstone_block: builtins.int = ...,
    ) -> None: ...
    def ClearField(
        self,
        field_name: typing_extensions.Literal[
            "fee",
            b"fee",
            "inputs",
            b"inputs",
            "outputs",
            b"outputs",
            "tombstone_block",
            b"tombstone_block",
        ],
    ) -> None: ...

global___TxPrefix = TxPrefix

class RingMLSAG(google.protobuf.message.Message):
    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    C_ZERO_FIELD_NUMBER: builtins.int
    RESPONSES_FIELD_NUMBER: builtins.int
    KEY_IMAGE_FIELD_NUMBER: builtins.int
    @property
    def c_zero(self) -> global___CurveScalar: ...
    @property
    def responses(
        self,
    ) -> google.protobuf.internal.containers.RepeatedCompositeFieldContainer[
        global___CurveScalar
    ]: ...
    @property
    def key_image(self) -> global___KeyImage: ...
    def __init__(
        self,
        *,
        c_zero: typing.Optional[global___CurveScalar] = ...,
        responses: typing.Optional[typing.Iterable[global___CurveScalar]] = ...,
        key_image: typing.Optional[global___KeyImage] = ...,
    ) -> None: ...
    def HasField(
        self,
        field_name: typing_extensions.Literal[
            "c_zero", b"c_zero", "key_image", b"key_image"
        ],
    ) -> builtins.bool: ...
    def ClearField(
        self,
        field_name: typing_extensions.Literal[
            "c_zero", b"c_zero", "key_image", b"key_image", "responses", b"responses"
        ],
    ) -> None: ...

global___RingMLSAG = RingMLSAG

class SignatureRctBulletproofs(google.protobuf.message.Message):
    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    RING_SIGNATURES_FIELD_NUMBER: builtins.int
    PSEUDO_OUTPUT_COMMITMENTS_FIELD_NUMBER: builtins.int
    RANGE_PROOFS_FIELD_NUMBER: builtins.int
    @property
    def ring_signatures(
        self,
    ) -> google.protobuf.internal.containers.RepeatedCompositeFieldContainer[
        global___RingMLSAG
    ]: ...
    @property
    def pseudo_output_commitments(
        self,
    ) -> google.protobuf.internal.containers.RepeatedCompositeFieldContainer[
        global___CompressedRistretto
    ]: ...
    range_proofs: builtins.bytes = ...
    def __init__(
        self,
        *,
        ring_signatures: typing.Optional[typing.Iterable[global___RingMLSAG]] = ...,
        pseudo_output_commitments: typing.Optional[
            typing.Iterable[global___CompressedRistretto]
        ] = ...,
        range_proofs: builtins.bytes = ...,
    ) -> None: ...
    def ClearField(
        self,
        field_name: typing_extensions.Literal[
            "pseudo_output_commitments",
            b"pseudo_output_commitments",
            "range_proofs",
            b"range_proofs",
            "ring_signatures",
            b"ring_signatures",
        ],
    ) -> None: ...

global___SignatureRctBulletproofs = SignatureRctBulletproofs

class Tx(google.protobuf.message.Message):
    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    PREFIX_FIELD_NUMBER: builtins.int
    SIGNATURE_FIELD_NUMBER: builtins.int
    @property
    def prefix(self) -> global___TxPrefix:
        """The actual contents of the transaction."""
        pass
    @property
    def signature(self) -> global___SignatureRctBulletproofs:
        """The RingCT signature on the prefix."""
        pass
    def __init__(
        self,
        *,
        prefix: typing.Optional[global___TxPrefix] = ...,
        signature: typing.Optional[global___SignatureRctBulletproofs] = ...,
    ) -> None: ...
    def HasField(
        self,
        field_name: typing_extensions.Literal[
            "prefix", b"prefix", "signature", b"signature"
        ],
    ) -> builtins.bool: ...
    def ClearField(
        self,
        field_name: typing_extensions.Literal[
            "prefix", b"prefix", "signature", b"signature"
        ],
    ) -> None: ...

global___Tx = Tx

class TxHash(google.protobuf.message.Message):
    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    HASH_FIELD_NUMBER: builtins.int
    hash: builtins.bytes = ...
    """Hash of a single transaction."""
    def __init__(
        self,
        *,
        hash: builtins.bytes = ...,
    ) -> None: ...
    def ClearField(
        self, field_name: typing_extensions.Literal["hash", b"hash"]
    ) -> None: ...

global___TxHash = TxHash

class Receipt(google.protobuf.message.Message):
    """Given to the recipient of a transaction output by the sender so that the
    recipient may verify that the other party is indeed the sender.

    Often given to the recipient before the transaction is finalized so that
    the recipient may know to anticipate the arrival of a transaction output,
    as well as know who it's from, when to consider it as having surpassed
    the tombstone block, and the expected amount of the output.
    """

    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    PUBLIC_KEY_FIELD_NUMBER: builtins.int
    CONFIRMATION_FIELD_NUMBER: builtins.int
    TOMBSTONE_BLOCK_FIELD_NUMBER: builtins.int
    AMOUNT_FIELD_NUMBER: builtins.int
    @property
    def public_key(self) -> global___CompressedRistretto:
        """Public key of the TxOut."""
        pass
    @property
    def confirmation(self) -> global___TxOutConfirmationNumber:
        """Confirmation number of the TxOut."""
        pass
    tombstone_block: builtins.int = ...
    """Tombstone block of the Tx that produced the TxOut.
    Note: This value is self-reported by the sender and is unverifiable.
    """
    @property
    def amount(self) -> global___Amount:
        """Amount of the TxOut.
        Note: This value is self-reported by the sender and is unverifiable.
        """
        pass
    def __init__(
        self,
        *,
        public_key: typing.Optional[global___CompressedRistretto] = ...,
        confirmation: typing.Optional[global___TxOutConfirmationNumber] = ...,
        tombstone_block: builtins.int = ...,
        amount: typing.Optional[global___Amount] = ...,
    ) -> None: ...
    def HasField(
        self,
        field_name: typing_extensions.Literal[
            "amount",
            b"amount",
            "confirmation",
            b"confirmation",
            "public_key",
            b"public_key",
        ],
    ) -> builtins.bool: ...
    def ClearField(
        self,
        field_name: typing_extensions.Literal[
            "amount",
            b"amount",
            "confirmation",
            b"confirmation",
            "public_key",
            b"public_key",
            "tombstone_block",
            b"tombstone_block",
        ],
    ) -> None: ...

global___Receipt = Receipt

class VerificationSignature(google.protobuf.message.Message):
    """/ The signature over an IAS JSON reponse, created by Intel"""

    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    CONTENTS_FIELD_NUMBER: builtins.int
    contents: builtins.bytes = ...
    def __init__(
        self,
        *,
        contents: builtins.bytes = ...,
    ) -> None: ...
    def ClearField(
        self, field_name: typing_extensions.Literal["contents", b"contents"]
    ) -> None: ...

global___VerificationSignature = VerificationSignature

class VerificationReport(google.protobuf.message.Message):
    """/ The IAS verification report response encoded as a protocol buffer"""

    DESCRIPTOR: google.protobuf.descriptor.Descriptor = ...
    SIG_FIELD_NUMBER: builtins.int
    CHAIN_FIELD_NUMBER: builtins.int
    HTTP_BODY_FIELD_NUMBER: builtins.int
    @property
    def sig(self) -> global___VerificationSignature:
        """/ The IAS-generated signature over the response string"""
        pass
    @property
    def chain(
        self,
    ) -> google.protobuf.internal.containers.RepeatedScalarFieldContainer[
        builtins.bytes
    ]:
        """/ A list of byte strings representing the DER-encoded certificate
        / chain provided by IAS.
        """
        pass
    http_body: typing.Text = ...
    """/ The raw report body JSON, as a byte sequence"""
    def __init__(
        self,
        *,
        sig: typing.Optional[global___VerificationSignature] = ...,
        chain: typing.Optional[typing.Iterable[builtins.bytes]] = ...,
        http_body: typing.Text = ...,
    ) -> None: ...
    def HasField(
        self, field_name: typing_extensions.Literal["sig", b"sig"]
    ) -> builtins.bool: ...
    def ClearField(
        self,
        field_name: typing_extensions.Literal[
            "chain", b"chain", "http_body", b"http_body", "sig", b"sig"
        ],
    ) -> None: ...

global___VerificationReport = VerificationReport
