import streamlit as st


@st.fragment
def render_review_summary(matches, review_issues, priced_count):
    if not matches:
        return

    st.divider()
    st.subheader("Needs review")

    total_parts = len(matches)
    priced_count = max(0, min(int(priced_count or 0), total_parts))
    unresolved_count = max(0, total_parts - priced_count)
    issue_part_indexes = {issue.get("part_index") for issue in review_issues}

    r1, r2, r3, r4 = st.columns(4)
    with r1:
        st.metric("Detected parts", total_parts)
    with r2:
        st.metric("Priced", priced_count)
    with r3:
        st.metric("Unresolved", unresolved_count)
    with r4:
        st.metric("Warnings", len(review_issues))

    if unresolved_count:
        st.warning(
            f"Current estimate includes {priced_count} of {total_parts} detected components. "
            f"{unresolved_count} component(s) are not priced yet, so the estimate may be incomplete."
        )
    else:
        st.success(f"All {total_parts} detected components currently have a price selection.")

    if not review_issues:
        return

    st.caption(
        "Review the warnings below before relying on the estimate. "
        "Warnings do not block testing or quotation generation."
    )

    for issue in review_issues:
        warning_col, button_col = st.columns([5, 1.4], vertical_alignment="center")
        with warning_col:
            st.markdown(
                f'**{issue["part_name"]}**  \n'
                f'{issue["issue_type"]}: {issue["detail"]}'
            )
        with button_col:
            st.markdown(
                f'<a class="cp-review-link" href="#component-{issue["part_index"]}">'
                "Review component →</a>",
                unsafe_allow_html=True,
            )
