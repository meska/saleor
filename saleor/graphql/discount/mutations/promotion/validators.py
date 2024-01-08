from typing import TYPE_CHECKING, Union

from django.conf import settings
from django.core.exceptions import ValidationError
from graphene.utils.str_converters import to_camel_case

from .....discount import RewardValueType
from ....core.validators import validate_price_precision
from ...utils import PredicateType

if TYPE_CHECKING:
    from decimal import Decimal


def clean_promotion_rule(
    cleaned_input, errors, error_class, index=None, predicate_type=None, instance=None
):
    catalogue_predicate = cleaned_input.get("catalogue_predicate")
    checkout_and_order_predicate = cleaned_input.get("checkout_and_order_predicate")
    if instance:
        catalogue_predicate = catalogue_predicate or instance.catalogue_predicate
        checkout_and_order_predicate = (
            checkout_and_order_predicate or instance.checkout_and_order_predicate
        )
    invalid_predicates = _clean_predicates(
        cleaned_input,
        catalogue_predicate,
        checkout_and_order_predicate,
        errors,
        error_class,
        index,
        predicate_type,
        instance,
    )
    if not invalid_predicates:
        channel_currencies = _get_channel_currencies(cleaned_input, instance)
        _clean_catalogue_predicate(
            cleaned_input, catalogue_predicate, errors, error_class, index, instance
        )
        _clean_checkout_and_order_predicate(
            cleaned_input,
            checkout_and_order_predicate,
            channel_currencies,
            errors,
            error_class,
            index,
            instance,
        )
        _clean_reward(
            cleaned_input,
            catalogue_predicate,
            checkout_and_order_predicate,
            channel_currencies,
            errors,
            error_class,
            index,
            instance,
        )

    return cleaned_input


def _clean_predicates(
    cleaned_input,
    catalogue_predicate,
    checkout_and_order_predicate,
    errors,
    error_class,
    index,
    predicate_type,
    instance,
):
    """Validate if predicates are provided and if they aren't mixed.

    - At least one predicate is required - `catalogue` or `checkoutAndOrder` predicate.
    - Promotion can have only one predicate type, raise error if there are mixed.
    """
    if catalogue_predicate is None and checkout_and_order_predicate is None:
        for field in ["catalogue_predicate", "checkout_and_order_predicate"]:
            errors[field].append(
                ValidationError(
                    message=(
                        "At least one of predicates is required: "
                        "'cataloguePredicate' or 'checkoutAndOrderPredicate'."
                    ),
                    code=error_class.REQUIRED.value,
                    params={"index": index} if index is not None else {},
                )
            )
        return True
    if catalogue_predicate and checkout_and_order_predicate:
        error_fields = ["catalogue_predicate", "checkout_and_order_predicate"]
        if instance:
            error_fields = [field for field in error_fields if field in cleaned_input]
        for field in error_fields:
            errors[field].append(
                ValidationError(
                    message=(
                        "Only one of predicates can be provided: "
                        "'cataloguePredicate' or 'checkoutAndOrderPredicate'."
                    ),
                    code=error_class.MIXED_PREDICATES.value,
                    params={"index": index} if index is not None else {},
                )
            )
        return True
    # the Promotion can have only rules with catalogue or checkoutAndOrder predicate
    elif (
        catalogue_predicate
        and predicate_type
        and predicate_type == PredicateType.CHECKOUT_AND_ORDER
    ):
        errors["catalogue_predicate"].append(
            ValidationError(
                message=(
                    "Predicate types can't be mixed. Given promotion already "
                    "have a rule with 'checkoutAndOrderPredicate' defined."
                ),
                code=error_class.MIXED_PROMOTION_PREDICATES.value,
                params={"index": index} if index is not None else {},
            )
        )
        return True
    elif (
        checkout_and_order_predicate
        and predicate_type
        and predicate_type == PredicateType.CATALOGUE
    ):
        errors["checkout_and_order_predicate"].append(
            ValidationError(
                message=(
                    "Predicate types can't be mixed. Given promotion already "
                    "have a rule with 'cataloguePredicate' defined."
                ),
                code=error_class.MIXED_PROMOTION_PREDICATES.value,
                params={"index": index} if index is not None else {},
            )
        )
        return True
    return False


def _clean_catalogue_predicate(
    cleaned_input, catalogue_predicate, errors, error_class, index, instance
):
    """Clean and validate catalogue predicate.

    - Reward type can't be specified for rule with catalogue predicate.
    """

    if not catalogue_predicate:
        return

    reward_type = cleaned_input.get("reward_type")
    if instance and "reward_type" not in cleaned_input:
        reward_type = reward_type or instance.reward_type
    if reward_type:
        errors["reward_type"].append(
            ValidationError(
                message=(
                    "The rewardType can't be specified for rule "
                    "with cataloguePredicate."
                ),
                code=error_class.INVALID.value,
                params={"index": index} if index is not None else {},
            )
        )
    else:
        if "catalogue_predicate" not in cleaned_input:
            return
        try:
            cleaned_input["catalogue_predicate"] = clean_predicate(
                catalogue_predicate,
                error_class,
                index,
            )
        except ValidationError as error:
            errors["catalogue_predicate"].append(error)


def _clean_checkout_and_order_predicate(
    cleaned_input,
    checkout_and_order_predicate,
    channel_currencies,
    errors,
    error_class,
    index,
    instance,
):
    """Clean and validate checkoutAndOrder predicate.

    - Reward type is required for rule with checkoutAndOrder predicate.
    - Price based predicates are allowed only for rules with one currency
    - Number of rules with checkoutAndOrder predicate doesn't exceed the limit
    """
    if not checkout_and_order_predicate:
        return

    reward_type = cleaned_input.get("reward_type")
    if "reward_type" not in cleaned_input and instance:
        reward_type = reward_type or instance.reward_type
    if not reward_type:
        errors["reward_type"].append(
            ValidationError(
                message=(
                    "The rewardType is required when "
                    "checkoutAndOrderPredicate is provided."
                ),
                code=error_class.REQUIRED.value,
                params={"index": index} if index is not None else {},
            )
        )
        return

    price_based_predicate = any(
        field in str(checkout_and_order_predicate)
        for field in ["subtotal_price", "subtotalPrice", "total_price", "totalPrice"]
    )
    if len(channel_currencies) > 1 and price_based_predicate:
        error_field = "channels"
        if instance:
            error_field = (
                "add_channels"
                if "add_channels" in cleaned_input
                else "checkout_and_order_predicate"
            )
        errors[error_field].append(
            ValidationError(
                message=(
                    "For price based predicates, all channels must have "
                    "the same currency."
                ),
                code=error_class.MULTIPLE_CURRENCIES_NOT_ALLOWED.value,
                params={"index": index} if index is not None else {},
            )
        )
        return

    if "checkout_and_order_predicate" not in cleaned_input:
        return

    try:
        cleaned_input["checkout_and_order_predicate"] = clean_predicate(
            checkout_and_order_predicate,
            error_class,
            index,
        )
    except ValidationError as error:
        errors["checkout_and_order_predicate"].append(error)
        return

    if promotion := cleaned_input.get("promotion"):
        rules_count = promotion.rules.count()
        rules_limit = settings.CHECKOUT_AND_ORDER_RULES_LIMIT
        # TODO: check the limit only for active promotions
        # https://github.com/saleor/saleor/issues/15201
        if rules_count >= int(rules_limit):
            errors["checkout_and_order_predicate"].append(
                ValidationError(
                    message=(
                        f"Number of rules has reached the limit of {rules_limit} "
                        f"rules per single promotion."
                    ),
                    code=error_class.RULES_NUMBER_LIMIT.value,
                )
            )


def _clean_reward(
    cleaned_input,
    catalogue_predicate,
    checkout_and_order_predicate,
    currencies,
    errors,
    error_class,
    index,
    instance,
):
    """Validate reward value and reward value type.

    - Fixed reward value type requires channels with the same currency code
    to be specified.
    - Validate price precision for fixed reward value.
    - Check if percentage reward value is not above 100.
    """
    if (
        instance
        and "reward_value" not in cleaned_input
        and "reward_value_type" not in cleaned_input
    ):
        return

    reward_value = cleaned_input.get("reward_value")
    reward_value_type = cleaned_input.get("reward_value_type")
    if instance:
        reward_value = (
            reward_value if "reward_value" in cleaned_input else instance.reward_value
        )
        reward_value_type = (
            reward_value_type
            if "reward_value_type" in cleaned_input
            else instance.reward_value_type
        )

    if reward_value_type is None and (
        catalogue_predicate or checkout_and_order_predicate
    ):
        errors["reward_value_type"].append(
            ValidationError(
                message=(
                    "The rewardValueType is required when "
                    "cataloguePredicate or checkoutAndOrderPredicate is provided."
                ),
                code=error_class.REQUIRED.value,
                params={"index": index} if index is not None else {},
            )
        )
    if reward_value is None and (catalogue_predicate or checkout_and_order_predicate):
        errors["reward_value"].append(
            ValidationError(
                message=(
                    "The rewardValue is required when "
                    "cataloguePredicate or checkoutAndOrderPredicate is provided."
                ),
                code=error_class.REQUIRED.value,
                params={"index": index} if index is not None else {},
            )
        )
    if reward_value and reward_value_type:
        _clean_reward_value(
            cleaned_input,
            reward_value,
            reward_value_type,
            currencies,
            errors,
            error_class,
            index,
            instance,
        )


def _get_channel_currencies(cleaned_input, instance) -> set[str]:
    """Get currencies for which the rules apply."""
    if not instance:
        channels = cleaned_input.get("channels", [])
        return {channel.currency_code for channel in channels}

    channel_currencies = set(instance.channels.values_list("currency_code", flat=True))
    if remove_channels := cleaned_input.get("remove_channels"):
        channel_currencies = channel_currencies - {
            channel.currency_code for channel in remove_channels
        }
    if add_channels := cleaned_input.get("add_channels"):
        channel_currencies.update([channel.currency_code for channel in add_channels])

    return channel_currencies


def _clean_reward_value(
    cleaned_input,
    reward_value,
    reward_value_type,
    channel_currencies,
    errors,
    error_class,
    index,
    instance,
):
    """Validate reward value and reward value type.

    - The Fixed reward value type requires channels with the same currency code.
    - Validate price precision for fixed reward value.
    - Check if percentage reward value is not above 100.
    """
    if reward_value_type == RewardValueType.FIXED:
        if "channels" in errors:
            return
        if not channel_currencies:
            error_field = "channels"
            if instance:
                error_field = (
                    "reward_value_type"
                    if "reward_value_type" in cleaned_input
                    else "remove_channels"
                )
            errors[error_field].append(
                ValidationError(
                    "Channels must be specified for FIXED rewardValueType.",
                    code=error_class.MISSING_CHANNELS.value,
                    params={"index": index} if index is not None else {},
                )
            )
            return
        if len(channel_currencies) > 1:
            error_code = error_class.MULTIPLE_CURRENCIES_NOT_ALLOWED.value
            error_field = "reward_value_type"
            if instance:
                error_field = (
                    "reward_value_type"
                    if "reward_value_type" in cleaned_input
                    else "add_channels"
                )
            errors[error_field].append(
                ValidationError(
                    "For FIXED rewardValueType, all channels must have "
                    "the same currency.",
                    code=error_code,
                    params={"index": index} if index is not None else {},
                )
            )
            return

        currency = channel_currencies.pop()
        try:
            clean_fixed_discount_value(
                reward_value,
                error_class.INVALID_PRECISION.value,
                currency,
                index,
            )
        except ValidationError as error:
            errors["reward_value"].append(error)

    elif reward_value_type == RewardValueType.PERCENTAGE:
        try:
            clean_percentage_discount_value(
                reward_value, error_class.INVALID.value, index
            )
        except ValidationError as error:
            errors["reward_value"].append(error)


def clean_predicate(predicate, error_class, index=None):
    """Validate operators and convert snake cases keys into camel case.

    Operators cannot be mixed with other filter inputs. There could be only
    one operator on each level.
    """
    if isinstance(predicate, list):
        return [
            clean_predicate(item, error_class, index)
            if isinstance(item, (dict, list))
            else item
            for item in predicate
        ]
    # when any operator appear there cannot be any more data in filter input
    if _contains_operator(predicate) and len(predicate.keys()) > 1:
        raise ValidationError(
            "Cannot mix operators with other filter inputs.",
            code=error_class.INVALID.value,
            params={"index": index} if index is not None else {},
        )
    return {
        to_camel_case(key): clean_predicate(value, error_class, index)
        if isinstance(value, (dict, list))
        else value
        for key, value in predicate.items()
    }


def _contains_operator(input: dict[str, Union[dict, str]]):
    return any([operator in input for operator in ["AND", "OR"]])


def clean_fixed_discount_value(
    reward_value: "Decimal", error_code: str, currency_code: str, index=None
):
    try:
        validate_price_precision(reward_value, currency_code)
    except ValidationError:
        raise ValidationError(
            "Invalid amount precision.",
            code=error_code,
            params={"index": index} if index is not None else {},
        )


def clean_percentage_discount_value(
    reward_value: "Decimal", error_code: str, index=None
):
    if reward_value > 100:
        raise ValidationError(
            "Invalid percentage value.",
            code=error_code,
            params={"index": index} if index is not None else {},
        )
