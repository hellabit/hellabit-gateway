// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.38;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {AccessControlDefaultAdminRules} from "@openzeppelin/contracts/access/extensions/AccessControlDefaultAdminRules.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";

/// @notice White-label issuance controls. Reserve assertions require an external
/// bank/custodian attestor; this contract does not establish fiat backing.
contract AttestedStablecoin is ERC20, AccessControlDefaultAdminRules, Pausable {
    bytes32 public constant MINTER_ROLE = keccak256("MINTER_ROLE");
    bytes32 public constant ATTESTOR_ROLE = keccak256("ATTESTOR_ROLE");
    bytes32 public constant SETTLER_ROLE = keccak256("SETTLER_ROLE");
    bytes32 public constant COMPLIANCE_ROLE = keccak256("COMPLIANCE_ROLE");
    bytes32 public immutable issuerId;
    bytes3 public immutable referenceCurrency;
    uint256 public reserveCeiling;
    uint64 public reserveValidUntil;
    uint256 public pendingRedemptions;
    mapping(bytes32 => bool) public usedMintRequests;
    mapping(bytes32 => bool) public usedAttestations;
    mapping(bytes32 => bool) public usedPayoutProofs;
    mapping(address => bool) public frozen;
    struct Redemption { address holder; uint256 amount; bool paid; }
    mapping(bytes32 => Redemption) public redemptions;

    event ReserveAttested(bytes32 indexed proofHash, uint256 ceiling, uint64 validUntil);
    event Issued(bytes32 indexed requestId, address indexed holder, uint256 amount);
    event RedemptionRequested(bytes32 indexed requestId, address indexed holder, uint256 amount);
    event RedemptionPaid(bytes32 indexed requestId, bytes32 indexed paymentProof);
    event FreezeChanged(address indexed holder, bool frozen);

    constructor(string memory name_, string memory symbol_, bytes32 issuer_, bytes3 currency_, address admin)
        ERC20(name_, symbol_) AccessControlDefaultAdminRules(2 days, admin) {
        require(issuer_ != bytes32(0), "issuer required");
        require(bytes(name_).length > 0 && bytes(name_).length <= 64 && bytes(symbol_).length >= 3 && bytes(symbol_).length <= 8, "brand required");
        require(currency_ == bytes3("USD") || currency_ == bytes3("CAD"), "reference currency");
        issuerId = issuer_; referenceCurrency = currency_;
    }
    function decimals() public pure override returns (uint8) { return 6; }
    function attestReserve(uint256 ceiling, uint64 validUntil, bytes32 proofHash) external onlyRole(ATTESTOR_ROLE) {
        require(proofHash != bytes32(0) && !usedAttestations[proofHash], "attestation replay");
        require(validUntil > block.timestamp && validUntil <= block.timestamp + 1 days, "attestation expiry");
        require(ceiling >= totalSupply() + pendingRedemptions, "below liabilities");
        usedAttestations[proofHash] = true;
        reserveCeiling = ceiling; reserveValidUntil = validUntil;
        emit ReserveAttested(proofHash, ceiling, validUntil);
    }
    function mint(address holder, uint256 amount, bytes32 requestId) external onlyRole(MINTER_ROLE) whenNotPaused {
        require(block.timestamp < reserveValidUntil, "reserve stale");
        require(amount > 0 && requestId != bytes32(0) && !usedMintRequests[requestId], "mint replay or amount");
        require(totalSupply() + pendingRedemptions + amount <= reserveCeiling, "reserve exceeded");
        usedMintRequests[requestId] = true;
        _mint(holder, amount); emit Issued(requestId, holder, amount);
    }
    function requestRedemption(uint256 amount, bytes32 requestId) external whenNotPaused {
        require(amount > 0 && requestId != bytes32(0) && redemptions[requestId].holder == address(0), "redemption replay or amount");
        _burn(msg.sender, amount);
        redemptions[requestId] = Redemption(msg.sender, amount, false);
        pendingRedemptions += amount;
        emit RedemptionRequested(requestId, msg.sender, amount);
    }
    function confirmRedemption(bytes32 requestId, bytes32 paymentProof) external onlyRole(SETTLER_ROLE) {
        Redemption storage item = redemptions[requestId];
        require(item.holder != address(0) && !item.paid && paymentProof != bytes32(0) && !usedPayoutProofs[paymentProof], "redemption state");
        usedPayoutProofs[paymentProof] = true;
        item.paid = true; pendingRedemptions -= item.amount; reserveCeiling -= item.amount;
        emit RedemptionPaid(requestId, paymentProof);
    }
    function setFrozen(address holder, bool value) external onlyRole(COMPLIANCE_ROLE) {
        require(holder != address(0), "holder required"); frozen[holder] = value;
        emit FreezeChanged(holder, value);
    }
    function pause() external onlyRole(COMPLIANCE_ROLE) { _pause(); }
    function unpause() external onlyRole(COMPLIANCE_ROLE) { _unpause(); }
    function _update(address from, address to, uint256 amount) internal override whenNotPaused {
        require(!frozen[from] && !frozen[to], "holder frozen"); super._update(from, to, amount);
    }
}
