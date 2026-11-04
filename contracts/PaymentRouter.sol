// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.38;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @notice Explicit buyer-signed settlement; proceeds go directly to merchant
/// and the named fee recipient. Deployment and bank/offramp are separate steps.
contract PaymentRouter is EIP712, ReentrancyGuard {
    using SafeERC20 for IERC20;
    address public immutable acceptedToken;
    struct Payment {
        address buyer; address merchant; address token; address feeRecipient;
        uint256 amount; uint256 fee; uint256 deadline; bytes32 operationId;
    }
    bytes32 public constant PAYMENT_TYPEHASH = keccak256("Payment(address buyer,address merchant,address token,address feeRecipient,uint256 amount,uint256 fee,uint256 deadline,bytes32 operationId)");
    mapping(address => mapping(bytes32 => bool)) public settled;
    event PaymentSettled(bytes32 indexed operationId, address indexed buyer, address indexed merchant,
        address token, uint256 amount, uint256 fee, address feeRecipient);
    constructor(address token_) EIP712("Hellabit Payment Router", "1") {
        require(token_.code.length > 0, "token contract required"); acceptedToken = token_;
    }
    function settle(Payment calldata p, bytes calldata signature) external nonReentrant {
        require(p.buyer != address(0) && p.merchant != address(0) && p.token != address(0) && p.feeRecipient != address(0), "address required");
        require(p.token == acceptedToken && p.merchant != address(this) && p.feeRecipient != address(this), "settlement binding");
        require(p.amount > 0 && p.fee <= p.amount && p.operationId != bytes32(0), "amount or id");
        require(block.timestamp <= p.deadline && !settled[p.buyer][p.operationId], "expired or replay");
        bytes32 digest = _hashTypedDataV4(keccak256(abi.encode(PAYMENT_TYPEHASH, p.buyer, p.merchant,
            p.token, p.feeRecipient, p.amount, p.fee, p.deadline, p.operationId)));
        require(ECDSA.recover(digest, signature) == p.buyer, "buyer signature");
        settled[p.buyer][p.operationId] = true;
        IERC20(p.token).safeTransferFrom(p.buyer, p.merchant, p.amount - p.fee);
        if (p.fee > 0) IERC20(p.token).safeTransferFrom(p.buyer, p.feeRecipient, p.fee);
        emit PaymentSettled(p.operationId, p.buyer, p.merchant, p.token, p.amount, p.fee, p.feeRecipient);
    }
}
