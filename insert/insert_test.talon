# #i When trying to trigger incorrect input, you can say "[command below] [newline command]" and then something like "again twenty times". Some apps do more and more work after each single character as the text box contents get longer, making incorrect input more probable.
# 
# # Texts to try to trigger character mixups (specifically in Qt apps like gImageReader). (The emojis are Unicode supplementary characters.)
# mix up short: insert("Five_emojis:_😀😌😐😒😠_Old-style_emojis:_🗩🗪🗫🗸🗹🗴🗵 — crème brûlée")
# mix up long: insert("staaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaay_tuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuned_Five_emojis:_😀😌😐😒😠_Old-style_emojis:_🗩🗪🗫🗸🗹🗴🗵 — Many_emojis:_🥲😅😂🤣😭😨😱😡👽🥶 Nguyễn Phước eats crème brûlée à la mode at a café.")
# 
# # Interaction of control keys with suggestion overlays. (Type "foobar\n" first. Maybe use different suggestion overlay app settings.)
# testing vee kays: insert("foo\tqux\n")
# # testing vee kays: insert("foo\nqux\n")
# # testing vee kays: insert("foo \b")
# # testing vee kays: insert("foo\N{ESC}")
